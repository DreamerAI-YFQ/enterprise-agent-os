import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Avatar, Button } from "@eaos/shared";
import { useAuth } from "@eaos/shared";
import { LogOut, ChevronDown } from "lucide-react";
import { cn } from "@eaos/shared";

export function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const initials = user?.name?.slice(0, 2).toUpperCase() ?? "?";

  async function handleLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-md px-1.5 py-1 transition-colors hover:bg-subtle"
      >
        <Avatar fallback={initials} size="sm" />
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 text-tertiary transition-transform",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-full z-popover mt-2 w-56 rounded-md border border-border bg-elevated p-2 shadow-md">
          <div className="border-b border-border-subtle px-2 pb-2 pt-1">
            <p className="text-sm font-medium text-foreground">
              {user?.name ?? "用户"}
            </p>
            <p className="truncate text-xs text-tertiary">{user?.email}</p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            className="mt-1 w-full justify-start text-danger hover:bg-danger-subtle"
          >
            <LogOut className="h-4 w-4" />
            退出登录
          </Button>
        </div>
      )}
    </div>
  );
}
