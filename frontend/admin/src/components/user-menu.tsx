import { useNavigate } from "react-router-dom";
import { useAuth } from "@eaos/shared";
import { ChevronDown, LogOut } from "lucide-react";
import { useState, useRef, useEffect } from "react";

export function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  if (!user) return null;

  const initials = user.name
    ? user.name.charAt(0).toUpperCase()
    : user.email.charAt(0).toUpperCase();

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 rounded-md py-1 pl-1 pr-2 transition-colors hover:bg-subtle"
      >
        <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-xs font-medium text-white">
          {initials}
        </div>
        <span className="text-sm text-foreground">{user.name}</span>
        <ChevronDown className="h-3.5 w-3.5 text-tertiary" />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 w-48 overflow-hidden rounded-md border border-border bg-elevated py-1 shadow-lg">
          <div className="border-b border-border-subtle px-3 py-2">
            <p className="text-xs font-medium text-foreground">{user.name}</p>
            <p className="text-xs text-tertiary">{user.email}</p>
          </div>
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-danger transition-colors hover:bg-subtle"
          >
            <LogOut className="h-3.5 w-3.5" />
            退出登录
          </button>
        </div>
      )}
    </div>
  );
}
