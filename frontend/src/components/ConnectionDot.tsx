import { cn } from "@/lib/utils";

interface Props {
  connected: boolean;
  botRunning?: boolean;
}

export function ConnectionDot({ connected, botRunning }: Props) {
  const color = connected
    ? botRunning
      ? "bg-emerald-500"
      : "bg-blue-500"
    : "bg-red-500";

  const label = connected
    ? botRunning
      ? "Connected · Bot running"
      : "Connected · Bot idle"
    : "Disconnected from backend";

  return (
    <span className="relative flex h-2.5 w-2.5" title={label}>
      {connected && (
        <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-75", color)} />
      )}
      <span className={cn("relative inline-flex h-2.5 w-2.5 rounded-full transition-colors", color)} />
    </span>
  );
}
