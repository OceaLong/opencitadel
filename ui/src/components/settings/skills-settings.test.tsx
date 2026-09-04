// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it } from "vitest";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import { SkillToolAccessBadge } from "./skills-settings";

afterEach(() => {
  document.body.replaceChildren();
});

function wrap(allowedTools: string[] | null) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      <SkillToolAccessBadge allowedTools={allowedTools} />
    </NextIntlClientProvider>
  );
}

describe("SkillToolAccessBadge", () => {
  it("renders a warning badge with an explanatory tooltip when tools are unrestricted (null)", async () => {
    const { container, unmount } = await renderComponent(wrap(null));

    const badge = container.querySelector<HTMLElement>('[data-testid="skill-tools-unrestricted"]');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toBe(en.settingsSkills.toolsUnrestricted);
    expect(badge?.title).toBe(en.settingsSkills.toolsUnrestrictedHint);
    await unmount();
  });

  it("renders an all-tools-disabled badge for an empty allowlist ([])", async () => {
    const { container, unmount } = await renderComponent(wrap([]));

    const badge = container.querySelector<HTMLElement>('[data-testid="skill-tools-all-disabled"]');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toBe(en.settingsSkills.toolsAllDisabled);
    expect(badge?.title).toBe(en.settingsSkills.toolsAllDisabledHint);
    await unmount();
  });

  it("renders nothing for a non-empty allowlist (existing whitelist display stays)", async () => {
    const { container, unmount } = await renderComponent(wrap(["read_file", "mcp_*"]));

    expect(container.textContent).toBe("");
    await unmount();
  });
});
