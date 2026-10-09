import { createMemoryRouter, RouterProvider, useNavigate, useLocation } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import { useRouteTab } from "./useRouteTab";
import { TabBar } from "@/components/common/TabBar";

function Page() {
  const [tab, setTab] = useRouteTab(["overview", "logs"] as const, "overview");
  const navigate = useNavigate();
  const location = useLocation();
  return <><TabBar tabs={[{ key: "overview", label: "Overview" }, { key: "logs", label: "Logs" }]} activeTab={tab} onChange={setTab} /><output>{location.search}</output><button onClick={() => navigate(-1)}>Back</button></>;
}
it("preserves other search parameters and browser history", async () => {
  const user = userEvent.setup();
  render(<RouterProvider router={createMemoryRouter([{ path: "/", element: <Page /> }], { initialEntries: ["/?level=ERROR"] })} />);
  await user.click(screen.getByRole("tab", { name: "Logs" }));
  expect(screen.getByRole("status").textContent).toBe("?level=ERROR&tab=logs");
  await user.click(screen.getByText("Back"));
  expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("status").textContent).toBe("?level=ERROR");
});
it("supports keyboard tab navigation", async () => {
  const user = userEvent.setup();
  render(<RouterProvider router={createMemoryRouter([{ path: "/", element: <Page /> }])} />);
  screen.getByRole("tab", { name: "Overview" }).focus();
  await user.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Logs" }).getAttribute("aria-selected")).toBe("true");
  expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Logs" }));
});
