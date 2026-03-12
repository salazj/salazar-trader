import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Settings,
  ScrollText,
  PieChart,
  ShieldAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { ConnectionDot } from "./ConnectionDot";
import { useBotStatus } from "@/hooks/useBotStatus";

const links = [
  { to: "/", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/config", icon: Settings, label: "Configuration" },
  { to: "/logs", icon: ScrollText, label: "Live Logs" },
  { to: "/portfolio", icon: PieChart, label: "Portfolio" },
  { to: "/risk", icon: ShieldAlert, label: "Risk Controls" },
];

export function Sidebar() {
  const { connected, botStatus } = useBotStatus();

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-60 md:flex-col md:fixed md:inset-y-0 border-r bg-card z-30">
        <div className="flex h-14 items-center justify-between border-b px-4">
          <span className="text-lg font-bold tracking-tight">$alazar-Trader</span>
          <ConnectionDot connected={connected} botRunning={botStatus.running} />
        </div>
        <nav className="flex-1 space-y-1 px-2 py-4">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                )
              }
            >
              <l.icon className="h-4 w-4" />
              {l.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t px-4 py-3">
          <p className="text-[10px] text-muted-foreground/50 uppercase tracking-widest">v3.0.0</p>
        </div>
      </aside>

      {/* Mobile bottom bar */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-40 border-t bg-card/95 backdrop-blur-sm flex justify-around py-2 safe-area-pb">
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className={({ isActive }) =>
              cn(
                "flex flex-col items-center gap-0.5 text-[11px] font-medium transition-colors p-1 min-w-[52px]",
                isActive ? "text-primary" : "text-muted-foreground"
              )
            }
          >
            <l.icon className="h-5 w-5" />
            <span>{l.label}</span>
          </NavLink>
        ))}
      </nav>
    </>
  );
}
