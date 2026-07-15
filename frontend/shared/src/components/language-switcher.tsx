import { useTranslation } from "react-i18next";
import { Languages } from "lucide-react";
import { setLocale, type AppLocale } from "../i18n";
import { cn } from "../lib/utils";

export interface LanguageSwitcherProps {
  /** Optional className override for the button. */
  className?: string;
  /** Render a compact (icon-only) variant. */
  compact?: boolean;
}

/**
 * Toggle between supported locales. Renders a small button with the
 * target language label (i.e. clicking "English" switches to English).
 */
export function LanguageSwitcher({ className, compact = false }: LanguageSwitcherProps) {
  const { i18n, t } = useTranslation();
  const current = (i18n.language || "zh-CN") as AppLocale;
  const next: AppLocale = current === "zh-CN" ? "en-US" : "zh-CN";

  const handleToggle = () => setLocale(next);

  const title = current === "zh-CN" ? t("language.switchToEn") : t("language.switchToZh");

  return (
    <button
      type="button"
      onClick={handleToggle}
      title={title}
      aria-label={title}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-subtle hover:text-foreground",
        className,
      )}
    >
      <Languages className="h-3.5 w-3.5" />
      {!compact && <span>{t("language.switch")}</span>}
    </button>
  );
}
