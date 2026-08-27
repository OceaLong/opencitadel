import { describe, expect, it } from "vitest";

import type { InferenceBinding, InferenceModel } from "./inference";
import { bindingIsInherited, bindingSelectionValue, modelsForPurpose } from "./inference-cache";

describe("inference cache projections", () => {
  it("filters candidates by the closed purpose-to-kind rule", () => {
    const chat = { id: "chat", kind: "chat" } as InferenceModel;
    const embedding = { id: "embedding", kind: "embedding" } as InferenceModel;

    expect(modelsForPurpose([chat, embedding], "chat")).toEqual([chat]);
    expect(modelsForPurpose([chat, embedding], "rerank")).toEqual([chat]);
    expect(modelsForPurpose([chat, embedding], "embedding")).toEqual([embedding]);
  });

  it("represents global fallback as inherited instead of a removable workspace binding", () => {
    const globalBinding = {
      model_id: "chat",
      owner_user_id: null,
      team_id: null,
    } as InferenceBinding;
    const workspaceBinding = {
      model_id: "chat-override",
      owner_user_id: "user-1",
      team_id: null,
    } as InferenceBinding;

    expect(bindingIsInherited(globalBinding)).toBe(true);
    expect(bindingSelectionValue(globalBinding)).toBe("inherit");
    expect(bindingIsInherited(workspaceBinding)).toBe(false);
    expect(bindingSelectionValue(workspaceBinding)).toBe("chat-override");
    expect(bindingSelectionValue(undefined)).toBe("inherit");
  });
});
