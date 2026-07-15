import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { toast } from "../lib/toast-store";
import { cn } from "../lib/utils";
import { setTheme, type ThemePreference } from "../lib/theme";
import { setLocale, getStoredLocale, type AppLocale } from "../i18n";
import { useTranslation } from "react-i18next";
import { Sun, Moon, Monitor, Bot, Bell, Save, Languages } from "lucide-react";

interface MeResponse {
  id: string;
  preferences: Record<string, unknown>;
}

interface Agent {
  id: string;
  name: string;
}

interface PreferencesFormProps {
  /** Whether to render the default Agent selector (employee only). */
  showDefaultAgent?: boolean;
}

interface PreferenceValues {
  theme: ThemePreference;
  default_agent_id: string;
  notification_approvals: boolean;
  notification_contributions: boolean;
  notification_system: boolean;
}

const DEFAULTS: PreferenceValues = {
  theme: "system",
  default_agent_id: "",
  notification_approvals: true,
  notification_contributions: true,
  notification_system: false,
};

function fromRaw(raw: Record<string, unknown>): PreferenceValues {
  return {
    theme: (raw.theme as ThemePreference) ?? DEFAULTS.theme,
    default_agent_id: (raw.default_agent_id as string) ?? DEFAULTS.default_agent_id,
    notification_approvals:
      typeof raw.notification_approvals === "boolean"
        ? raw.notification_approvals
        : DEFAULTS.notification_approvals,
    notification_contributions:
      typeof raw.notification_contributions === "boolean"
        ? raw.notification_contributions
        : DEFAULTS.notification_contributions,
    notification_system:
      typeof raw.notification_system === "boolean"
        ? raw.notification_system
        : DEFAULTS.notification_system,
  };
}

const THEME_VALUES: ThemePreference[] = ["light", "dark", "system"];
const LOCALE_VALUES: AppLocale[] = ["zh-CN", "en-US"];

/**
 * Structured personal preference form — shared between admin and employee
 * settings pages. Backed by /me + /me/preferences endpoints.
 */
export function PreferencesForm({ showDefaultAgent = false }: PreferencesFormProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [values, setValues] = useState<PreferenceValues>(DEFAULTS);
  const [locale, setLocaleState] = useState<AppLocale>(() => getStoredLocale());

  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/me", {});
      if (error || !data) return null;
      return data as unknown as MeResponse;
    },
  });

  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/agents", {});
      if (error || !data) return [] as Agent[];
      return data as unknown as Agent[];
    },
    enabled: showDefaultAgent,
  });

  useEffect(() => {
    if (meQuery.data?.preferences) {
      const parsed = fromRaw(meQuery.data.preferences);
      setValues(parsed);
    }
  }, [meQuery.data?.preferences]);

  const saveMutation = useMutation({
    mutationFn: async (prefs: PreferenceValues) => {
      await apiClient.PUT("/me/preferences", {
        body: { preferences: prefs as unknown as Record<string, unknown> },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me"] });
      toast.show({ title: t("common.saved"), variant: "success" });
    },
    onError: () => {
      toast.show({ title: t("common.error"), variant: "danger" });
    },
  });

  if (meQuery.isLoading) {
    return (
      <div className="flex h-32 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const update = <K extends keyof PreferenceValues>(key: K, val: PreferenceValues[K]) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    if (key === "theme") {
      setTheme(val as ThemePreference);
    }
  };

  const handleLocaleChange = (next: AppLocale) => {
    setLocaleState(next);
    setLocale(next);
    void i18n.changeLanguage(next);
  };

  const themeOptions = THEME_VALUES.map((value) => ({
    value,
    label: t(`settings.theme${value.charAt(0).toUpperCase()}${value.slice(1)}`),
    icon: value === "light" ? Sun : value === "dark" ? Moon : Monitor,
  }));

  const localeLabels: Record<AppLocale, string> = {
    "zh-CN": "中文（简体）",
    "en-US": "English (US)",
  };

  return (
    <section className="rounded-md border border-border bg-elevated p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-medium text-foreground">{t("settings.preferences")}</h2>
          <p className="mt-1 text-xs text-tertiary">{t("settings.preferencesDesc")}</p>
        </div>
        <Button
          size="sm"
          onClick={() => saveMutation.mutate(values)}
          disabled={saveMutation.isPending}
        >
          {saveMutation.isPending ? (
            t("common.loading")
          ) : (
            <>
              <Save className="h-3.5 w-3.5" />
              {t("common.save")}
            </>
          )}
        </Button>
      </div>

      <div className="mt-5 space-y-5">
        {/* Theme */}
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Sun className="h-4 w-4 text-tertiary" strokeWidth={1.75} />
            {t("settings.theme")}
          </label>
          <p className="mt-0.5 text-xs text-tertiary">{t("settings.themeDesc")}</p>
          <div className="mt-2 flex gap-2">
            {themeOptions.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => update("theme", opt.value)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                  values.theme === opt.value
                    ? "border-accent bg-accent/5 text-accent"
                    : "border-border bg-elevated text-secondary hover:bg-subtle",
                )}
              >
                <opt.icon className="h-4 w-4" strokeWidth={1.75} />
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Language */}
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Languages className="h-4 w-4 text-tertiary" strokeWidth={1.75} />
            {t("settings.language")}
          </label>
          <p className="mt-0.5 text-xs text-tertiary">{t("settings.languageDesc")}</p>
          <div className="mt-2 flex gap-2">
            {LOCALE_VALUES.map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => handleLocaleChange(value)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                  locale === value
                    ? "border-accent bg-accent/5 text-accent"
                    : "border-border bg-elevated text-secondary hover:bg-subtle",
                )}
              >
                {localeLabels[value]}
              </button>
            ))}
          </div>
        </div>

        {/* Default Agent */}
        {showDefaultAgent && (
          <div>
            <label className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Bot className="h-4 w-4 text-tertiary" strokeWidth={1.75} />
              {t("settings.defaultAgent")}
            </label>
            <p className="mt-0.5 text-xs text-tertiary">{t("settings.defaultAgentDesc")}</p>
            <select
              value={values.default_agent_id}
              onChange={(e) => update("default_agent_id", e.target.value)}
              className="mt-2 h-9 w-full rounded-md border border-border bg-elevated px-3 text-sm text-foreground focus:border-accent focus:outline-none"
            >
              <option value="">{t("settings.defaultAgentNone")}</option>
              {(agentsQuery.data ?? []).map((agent) => (
                <option key={agent.id} value={agent.id}>
                  {agent.name}
                </option>
              ))}
            </select>
          </div>
        )}

        {/* Notifications */}
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Bell className="h-4 w-4 text-tertiary" strokeWidth={1.75} />
            {t("settings.notifications")}
          </label>
          <p className="mt-0.5 text-xs text-tertiary">{t("settings.notificationsDesc")}</p>
          <div className="mt-2 space-y-2">
            <NotificationToggle
              label={t("settings.notificationApprovals")}
              description={t("settings.notificationApprovalsDesc")}
              checked={values.notification_approvals}
              onChange={(v) => update("notification_approvals", v)}
            />
            <NotificationToggle
              label={t("settings.notificationContributions")}
              description={t("settings.notificationContributionsDesc")}
              checked={values.notification_contributions}
              onChange={(v) => update("notification_contributions", v)}
            />
            <NotificationToggle
              label={t("settings.notificationSystem")}
              description={t("settings.notificationSystemDesc")}
              checked={values.notification_system}
              onChange={(v) => update("notification_system", v)}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function NotificationToggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between rounded-md border border-border bg-elevated px-3 py-2.5 transition-colors hover:bg-subtle/50">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-foreground">{label}</p>
        <p className="truncate text-xs text-tertiary">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative ml-3 h-5 w-9 shrink-0 rounded-full transition-colors",
          checked ? "bg-accent" : "bg-subtle",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform",
            checked ? "translate-x-4" : "translate-x-0.5",
          )}
        />
      </button>
    </label>
  );
}
