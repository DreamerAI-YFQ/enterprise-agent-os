import type { Config } from "tailwindcss";

/**
 * Tailwind theme extension mapping EAOS design tokens (CSS variables)
 * to Tailwind utility classes. Both apps (admin / employee) extend this
 * so the design language stays unified.
 */
export const eaosTheme: Pick<Config, "theme"> = {
  theme: {
    extend: {
      colors: {
        // Surfaces
        background: "var(--color-bg-base)",
        elevated: "var(--color-bg-elevated)",
        subtle: "var(--color-bg-subtle)",
        muted: "var(--color-bg-muted)",
        // Text
        foreground: "var(--color-text-primary)",
        secondary: "var(--color-text-secondary)",
        tertiary: "var(--color-text-tertiary)",
        // Brand
        accent: {
          DEFAULT: "var(--color-accent)",
          hover: "var(--color-accent-hover)",
          active: "var(--color-accent-active)",
          subtle: "var(--color-accent-subtle)",
        },
        // Status
        success: {
          DEFAULT: "var(--color-success)",
          subtle: "var(--color-success-subtle)",
        },
        warning: {
          DEFAULT: "var(--color-warning)",
          subtle: "var(--color-warning-subtle)",
        },
        danger: {
          DEFAULT: "var(--color-danger)",
          subtle: "var(--color-danger-subtle)",
        },
        info: {
          DEFAULT: "var(--color-info)",
          subtle: "var(--color-info-subtle)",
        },
        // Borders
        border: "var(--color-border)",
        "border-strong": "var(--color-border-strong)",
        "border-subtle": "var(--color-border-subtle)",
      },
      fontFamily: {
        sans: "var(--font-sans)",
        mono: "var(--font-mono)",
      },
      fontSize: {
        xs: ["var(--text-xs)", { lineHeight: "var(--text-xs--line-height)" }],
        sm: ["var(--text-sm)", { lineHeight: "var(--text-sm--line-height)" }],
        base: ["var(--text-base)", { lineHeight: "var(--text-base--line-height)" }],
        lg: ["var(--text-lg)", { lineHeight: "var(--text-lg--line-height)" }],
        xl: ["var(--text-xl)", { lineHeight: "var(--text-xl--line-height)" }],
        "2xl": ["var(--text-2xl)", { lineHeight: "var(--text-2xl--line-height)" }],
        "3xl": ["var(--text-3xl)", { lineHeight: "var(--text-3xl--line-height)" }],
      },
      spacing: {
        1: "var(--space-1)",
        2: "var(--space-2)",
        3: "var(--space-3)",
        4: "var(--space-4)",
        5: "var(--space-5)",
        6: "var(--space-6)",
        8: "var(--space-8)",
        10: "var(--space-10)",
        12: "var(--space-12)",
        16: "var(--space-16)",
      },
      borderRadius: {
        sm: "var(--radius-sm)",
        md: "var(--radius-md)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
        full: "var(--radius-full)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        focus: "var(--shadow-focus)",
      },
      transitionTimingFunction: {
        out: "var(--ease-out)",
        spring: "var(--ease-spring)",
      },
      transitionDuration: {
        fast: "var(--duration-fast)",
        base: "var(--duration-base)",
        slow: "var(--duration-slow)",
      },
      zIndex: {
        base: "var(--z-base)",
        sticky: "var(--z-sticky)",
        drawer: "var(--z-drawer)",
        popover: "var(--z-popover)",
        modal: "var(--z-modal)",
        toast: "var(--z-toast)",
      },
      width: {
        nav: "var(--nav-width)",
        "nav-collapsed": "var(--nav-width-collapsed)",
      },
      height: {
        topbar: "var(--topbar-height)",
      },
      maxWidth: {
        content: "var(--content-max)",
      },
    },
  },
};
