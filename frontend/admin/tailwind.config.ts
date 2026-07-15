import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";
// Import directly from source to avoid loading the shared barrel (which pulls
// React components that jiti — Tailwind's TS loader — cannot resolve).
import { eaosTheme } from "../shared/src/design/theme";

const config: Config = {
  darkMode: ["class", '[data-theme="dark"]'],
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "../shared/src/**/*.{ts,tsx}",
  ],
  theme: eaosTheme.theme,
  plugins: [animate],
};

export default config;
