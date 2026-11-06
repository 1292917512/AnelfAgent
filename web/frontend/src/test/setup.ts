import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";
import { i18nReady } from "@/i18n";

await i18nReady;

afterEach(cleanup);
Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value() {} });
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true }),
});
