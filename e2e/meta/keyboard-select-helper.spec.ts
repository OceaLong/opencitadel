import { expect, test } from "@playwright/test";
import {
  keyboardApproveTarget,
  keyboardChoose,
  keyboardReconcileReview,
} from "../support/keyboard-evaluation";

for (const selected of ["first", "second"]) {
  test(`native keyboard typeahead chooses exact ${selected} value among duplicate labels`, async ({
    page,
  }) => {
    await page.setContent(`
      <label>Source step <select>
        <option value="">Select</option>
        <option value="first">Workbench evidence</option>
        <option value="second">Workbench evidence</option>
        <option value="other">Other model evidence</option>
      </select></label><button>Inspect</button>
    `);
    const select = page.getByLabel("Source step");
    const changes: string[] = [];
    await page.exposeFunction("selectedByKeyboard", (value: string) =>
      changes.push(value),
    );
    await select.evaluate((node) =>
      node.addEventListener("change", () => {
        void (window as any).selectedByKeyboard(
          (node as HTMLSelectElement).value,
        );
      }),
    );
    await keyboardChoose(page, select, selected);
    await expect(select).toHaveValue(selected);
    expect(changes.at(-1)).toBe(selected);
  });
}

test("keyboard review accepts an asynchronously available context without losing the draft", async ({
  page,
}) => {
  await page.setContent(`
    <label>Dimension <select></select></label>
    <label>New value <input value="3"></label>
    <label>Reason <input value="Existing reviewed draft"></label>
    <button id="reconcile" hidden>Reconcile draft with current review</button>
    <button id="submit" disabled>Submit review</button>
  `);
  await page.evaluate(() => {
    const button = document.querySelector<HTMLButtonElement>("#reconcile")!;
    button.addEventListener("click", () => {
      document.querySelector("select")!.innerHTML =
        '<option value="correctness">Correctness</option>';
      document.querySelector<HTMLButtonElement>("#submit")!.disabled = false;
      button.hidden = true;
    });
    setTimeout(() => {
      button.hidden = false;
    }, 100);
  });
  await keyboardReconcileReview(page);
  await expect(page.getByRole("combobox", { name: "Dimension" })).toHaveValue(
    "correctness",
  );
  await expect(page.getByLabel("New value")).toHaveValue("3");
  await expect(page.getByLabel("Reason")).toHaveValue(
    "Existing reviewed draft",
  );
  await expect(
    page.getByRole("button", { name: "Submit review" }),
  ).toBeEnabled();
});

test("keyboard review preserves an already accepted current target", async ({
  page,
}) => {
  await page.setContent(`
    <label>Dimension <select><option value="correctness">Correctness</option></select></label>
    <button>Submit review</button>
  `);
  await keyboardReconcileReview(page);
  await expect(page.getByRole("combobox", { name: "Dimension" })).toHaveValue(
    "correctness",
  );
});

for (const expanded of [false, true]) {
  test(`keyboard approval handles ${expanded ? "expanded mobile" : "collapsed desktop"} conversation`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: expanded ? 390 : 1440, height: 900 });
    await page.setContent(`
      <button id="open" ${expanded ? "hidden" : ""}>Open approval actions</button>
      <section id="conversation" ${expanded ? "" : "hidden"}>
        <button id="approve">Approve</button><button>Reject</button>
      </section><output id="result"></output>
    `);
    await page.evaluate(() => {
      document.querySelector("#open")!.addEventListener("click", () => {
        document.querySelector<HTMLElement>("#conversation")!.hidden = false;
      });
      document.querySelector("#approve")!.addEventListener("click", (event) => {
        document.querySelector("#result")!.textContent =
          (event as MouseEvent).detail === 0
            ? "keyboard approved"
            : "pointer used";
      });
    });
    await keyboardApproveTarget(page);
    await expect(page.locator("#result")).toHaveText("keyboard approved");
  });
}

test("keyboard approval keeps an exact batch button bound to its owned row", async ({
  page,
}) => {
  await page.setContent(`
    <section id="other"><button>Approve</button></section>
    <section id="owned"><button>Approve</button></section>
    <output id="result"></output>
  `);
  await page.evaluate(() => {
    for (const id of ["other", "owned"])
      document
        .querySelector(`#${id} button`)!
        .addEventListener("click", (event) => {
          document.querySelector("#result")!.textContent =
            `${id}:${(event as MouseEvent).detail === 0 ? "keyboard" : "pointer"}`;
        });
  });
  await keyboardApproveTarget(
    page,
    page.locator("#owned").getByRole("button", { name: "Approve" }),
  );
  await expect(page.locator("#result")).toHaveText("owned:keyboard");
});
