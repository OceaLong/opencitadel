import { vi } from "vitest";

/**
 * Reusable `vi.mock` factory helpers for the four modules that nearly every
 * component test in this repo re-mocks by hand: `next-intl`,
 * `next/navigation`, `sonner`, and the auth provider/hook.
 *
 * IMPORTANT — Vitest hoisting: `vi.mock(...)` calls are hoisted to the top
 * of the test file, above regular imports. That means the *call site* of
 * `vi.mock` must stay in the test file itself (you cannot re-export a
 * `vi.mock("next-intl", ...)` call from this module and have it apply to a
 * different file). What you *can* do — and what these helpers are for — is
 * import one of these factory functions and call it *inside* your own
 * `vi.mock` factory callback, exactly like `knowledge-library.test.tsx`
 * already did with its inline `vi.hoisted` mock objects:
 *
 * ```ts
 * import { mockNextIntl, mockNavigation, mockSonner, mockAuth } from "@/test-utils/mocks";
 *
 * vi.mock("next-intl", () => mockNextIntl({ startAsk: "Ask", startAgent: "Agent" }));
 * vi.mock("next/navigation", () => mockNavigation({ push: mocks.push }));
 * vi.mock("sonner", () => mockSonner());
 * vi.mock("@/providers/auth-provider", () => mockAuth());
 * ```
 *
 * This works because ESM imports execute depth-first, in source order, as
 * each `import` statement is reached — so as long as `@/test-utils/mocks`
 * is imported *before* anything that transitively imports one of the
 * mocked modules (e.g. the component under test importing `next-intl`),
 * `mockNextIntl` etc. are already initialized by the time the mock
 * factory runs.
 *
 * Gotcha (found migrating `session-detail-resource-version.test.tsx`):
 * `vi.mock(...)` calls are hoisted above the file's own statements, but
 * that hoisting does not reorder *other* modules' imports relative to each
 * other. If the component under test is imported textually *before*
 * `@/test-utils/mocks` in the same file, loading that component can
 * recursively trigger the `next-intl` mock factory while
 * `@/test-utils/mocks` is still mid-evaluation, throwing "Cannot access
 * '...' before initialization". Fix: import `@/test-utils/mocks` (and
 * `@/test-utils/render`) before the component under test — or, as this
 * repo's existing tests already do, import the component under test last,
 * after all `vi.mock(...)` calls.
 *
 * Values that need to be asserted on or reset
 * between tests (e.g. a router's `push` mock) should still live in the test
 * file's own `vi.hoisted(...)` block and be passed in as overrides, so the
 * test keeps a stable reference to the same `vi.fn()`.
 */

type TranslationValue = string | ((values?: Record<string, unknown>) => string);
type TranslationMap = Record<string, TranslationValue>;

/**
 * Builds a `next-intl` module mock. `map` may associate a key with either a
 * literal string, or a function of the `values` passed to `t(key, values)`
 * for keys that need interpolation (mirrors the ad-hoc
 * `` `Confirm ${values?.version ?? ""}` `` style translators duplicated
 * across the pre-migration tests). Unmapped keys fall back to `key` (or
 * `` `${key}:${JSON.stringify(values)}` `` when called with values), which
 * matches the previous per-file default behavior.
 */
export function mockNextIntl(map: TranslationMap = {}, locale = "en") {
  // 真实 next-intl 的 useTranslations 返回 useMemo 过的稳定引用；这里同样
  // 返回单例翻译函数，避免把 t 放进 effect 依赖的组件在测试里无限重渲染。
  const translate = (key: string, values?: Record<string, unknown>): string => {
    const entry = map[key];
    if (typeof entry === "function") return entry(values);
    if (entry !== undefined) return entry;
    return values ? `${key}:${JSON.stringify(values)}` : key;
  };
  return {
    useLocale: () => locale,
    useTranslations: () => translate,
  };
}

/**
 * Builds a `next/navigation` module mock. Pass in `vi.fn()`s already held by
 * a `vi.hoisted(...)` block if the test needs to assert on `push`/`replace`
 * calls or reset them between tests; otherwise fresh `vi.fn()`s are used.
 */
export function mockNavigation(router: { push?: unknown; replace?: unknown } = {}) {
  const { push = vi.fn(), replace = vi.fn() } = router;
  return {
    useRouter: () => ({ push, replace }),
  };
}

/** Builds a `sonner` module mock exposing `toast.error` / `toast.success`. */
export function mockSonner() {
  return {
    toast: { error: vi.fn(), success: vi.fn() },
  };
}

/** Builds an `@/providers/auth-provider` mock returning a signed-in user. */
export function mockAuth(user: { id: string } = { id: "user-1" }) {
  return {
    useAuth: () => ({ user }),
  };
}
