import {
  constants,
  closeSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { createHash } from "node:crypto";
import { dirname, join, resolve, sep } from "node:path";
import { tmpdir } from "node:os";
import type { OwnedActorCleanup } from "./cleanup-journal";

type Recovery = {
  schema_version: 1;
  run_id: string;
  actor: OwnedActorCleanup;
  password: string;
  restore_token?: string;
};
function location(actor: OwnedActorCleanup, env: NodeJS.ProcessEnv): string {
  if (
    !env.ACCEPTANCE_RUN_ID ||
    !env.ACCEPTANCE_EVIDENCE_DIR ||
    !/^[a-f0-9-]{36}$/.test(actor.recovery_id)
  )
    throw new Error("actor recovery binding required");
  const root = resolve(
    env.ACCEPTANCE_ACTOR_RECOVERY_DIR ??
      join(
        realpathSync(tmpdir()),
        `opencitadel-actor-${process.getuid!()}-${createHash("sha256").update(resolve(env.ACCEPTANCE_EVIDENCE_DIR)).digest("hex")}`,
      ),
  );
  const evidence = resolve(env.ACCEPTANCE_EVIDENCE_DIR);
  if (root === evidence || root.startsWith(evidence + sep))
    throw new Error("actor recovery must be outside published evidence");
  mkdirSync(root, { mode: 0o700, recursive: true });
  const info = lstatSync(root);
  if (info.isSymbolicLink()) throw new Error("actor recovery symlink refused");
  if (
    !info.isDirectory() ||
    info.uid !== process.getuid!() ||
    (info.mode & 0o777) !== 0o700
  )
    throw new Error("actor recovery directory ownership mismatch");
  // Reject aliased ancestor paths as well; /tmp itself is canonicalized by callers on macOS.
  for (
    let parent = dirname(root);
    parent !== dirname(parent);
    parent = dirname(parent)
  ) {
    if (lstatSync(parent).isSymbolicLink())
      throw new Error("actor recovery ancestor symlink refused");
  }
  return join(root, actor.recovery_id + ".json");
}
function syncDirectory(path: string): void {
  const fd = openSync(dirname(path), constants.O_RDONLY);
  try {
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}
export function saveActorRecovery(
  value: OwnedActorCleanup & { password: string },
  env: NodeJS.ProcessEnv = process.env,
): { path: string } {
  const { password, ...actor } = value;
  if (
    !password ||
    actor.resource_id !== actor.registration.user_id ||
    actor.workspace_id !== actor.registration.team_id
  )
    throw new Error("actor recovery binding mismatch");
  const path = location(actor, env);
  const fd = openSync(
    path,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_EXCL |
      constants.O_NOFOLLOW,
    0o600,
  );
  try {
    writeFileSync(
      fd,
      JSON.stringify({
        schema_version: 1,
        run_id: env.ACCEPTANCE_RUN_ID,
        actor,
        password,
      }),
    );
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  syncDirectory(path);
  return { path };
}
export function readActorRecovery(
  actor: OwnedActorCleanup,
  env: NodeJS.ProcessEnv = process.env,
): Recovery {
  const path = location(actor, env);
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = fstatSync(fd);
    if (
      !info.isFile() ||
      info.uid !== process.getuid!() ||
      (info.mode & 0o777) !== 0o600 ||
      info.nlink !== 1
    )
      throw new Error("actor recovery file ownership mismatch");
    const saved = JSON.parse(readFileSync(fd, "utf8")) as Recovery;
    if (
      saved.schema_version !== 1 ||
      saved.run_id !== env.ACCEPTANCE_RUN_ID ||
      JSON.stringify(saved.actor) !== JSON.stringify(actor) ||
      !saved.password
    )
      throw new Error("actor recovery binding mismatch");
    return saved;
  } finally {
    closeSync(fd);
  }
}
export function removeActorRecovery(
  actor: OwnedActorCleanup,
  env: NodeJS.ProcessEnv = process.env,
): void {
  readActorRecovery(actor, env);
  const path = location(actor, env);
  unlinkSync(path);
  syncDirectory(path);
}

export function saveRestorationToken(
  actor: OwnedActorCleanup,
  token: string | undefined,
  env: NodeJS.ProcessEnv = process.env,
): void {
  if (token !== undefined && !/^[a-zA-Z0-9_-]{20,200}$/.test(token))
    throw new Error("invalid restoration token");
  const current = readActorRecovery(actor, env);
  const path = location(actor, env),
    temporary = path + ".pending";
  const fd = openSync(
    temporary,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_EXCL |
      constants.O_NOFOLLOW,
    0o600,
  );
  try {
    writeFileSync(fd, JSON.stringify({ ...current, restore_token: token }));
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(temporary, path);
  syncDirectory(path);
}
