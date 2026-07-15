import { NavLink, Outlet } from "react-router-dom";
import { navGroups, filterNavByRole } from "./nav-items";
import { UserMenu } from "../components/user-menu";
import { Sparkles } from "lucide-react";
import { cn, useAuth, NotificationBell, LanguageSwitcher, useTranslation } from "@eaos/shared";

export function AppLayout() {
  const { user } = useAuth();
  const { t } = useTranslation();
  const visibleGroups = filterNavByRole(navGroups, user?.role);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar */}
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-elevated">
        {/* Brand */}
        <div className="flex h-topbar items-center gap-2.5 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent">
            <Sparkles className="h-4 w-4 text-white" strokeWidth={2.5} />
          </div>
          <div className="flex flex-col leading-tight">
            <span className="text-base font-semibold text-foreground">
              {t("app.title")}
            </span>
            <span className="text-xs text-tertiary">{t("app.subtitle")}</span>
          </div>
        </div>

        {/* Nav Groups (role-filtered) */}
        <nav className="flex-1 overflow-y-auto px-3 py-3">
          {visibleGroups.map((group) => (
            <div key={group.labelKey} className="mb-4">
              <p className="mb-1 px-2 text-xs font-medium text-tertiary">
                {t(group.labelKey)}
              </p>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/admin"}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors duration-fast ease-out",
                        isActive
                          ? "bg-accent-subtle text-accent"
                          : "text-secondary hover:bg-subtle hover:text-foreground"
                      )
                    }
                  >
                    <item.icon className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                    {t(item.labelKey)}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="border-t border-border-subtle px-5 py-3">
          <p className="text-xs text-tertiary">{t("app.version")}</p>
        </div>
      </aside>

      {/* Main */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Topbar */}
        <header className="flex h-topbar shrink-0 items-center justify-between border-b border-border bg-elevated/80 px-6 backdrop-blur-md">
          <div className="text-sm font-medium text-secondary">
            {t("app.subtitle")}
          </div>
          <div className="flex items-center gap-3">
            <NotificationBell to="/admin/notifications" />
            <LanguageSwitcher />
            <UserMenu />
          </div>
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
