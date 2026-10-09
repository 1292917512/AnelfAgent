import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { Modal, ConfirmDialog } from "./Modal";

function Nested() {
  const [parent, setParent] = useState(false);
  const [child, setChild] = useState(false);
  return <><button onClick={() => setParent(true)}>Open parent</button>
    <Modal open={parent} onClose={() => setParent(false)} title="Parent"><button onClick={() => setChild(true)}>Open child</button>
      <Modal open={child} onClose={() => setChild(false)} title="Child"><button>Child action</button></Modal>
    </Modal></>;
}
it("Escape closes only the top dialog and restores focus", async () => {
  const user = userEvent.setup();
  render(<Nested />);
  await user.click(screen.getByText("Open parent"));
  await user.click(screen.getByText("Open child"));
  expect(screen.getByRole("dialog", { name: "Child" })).toBeTruthy();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog", { name: "Child" })).toBeNull();
  expect(screen.getByRole("dialog", { name: "Parent" })).toBeTruthy();
  await waitFor(() => expect(document.activeElement).toBe(screen.getByText("Open child")));
  await user.keyboard("{Escape}");
  await waitFor(() => expect(document.activeElement).toBe(screen.getByText("Open parent")));
});
it("cannot dismiss an in-flight confirmation", async () => {
  let closed = false;
  const user = userEvent.setup();
  render(<ConfirmDialog open title="Delete" loading onClose={() => { closed = true; }} onConfirm={() => {}} />);
  await user.keyboard("{Escape}");
  expect(closed).toBe(false);
  expect(screen.getByRole("dialog", { name: "Delete" })).toBeTruthy();
});
