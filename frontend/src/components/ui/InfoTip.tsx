import { useState } from "react";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface InfoTipProps {
  text: string;
  className?: string;
}

export function InfoTip({ text, className }: InfoTipProps) {
  const [show, setShow] = useState(false);

  return (
    <span className={cn("relative inline-flex items-center", className)}>
      <button
        type="button"
        className="text-muted-foreground/50 hover:text-muted-foreground transition-colors ml-1"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onClick={() => setShow((s) => !s)}
        aria-label="More information"
      >
        <HelpCircle className="h-3.5 w-3.5" />
      </button>
      {show && (
        <div className="absolute z-50 bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 px-3 py-2 rounded-lg bg-popover border border-border text-xs text-popover-foreground shadow-lg animate-in fade-in-0 slide-in-from-bottom-2">
          {text}
          <div className="absolute top-full left-1/2 -translate-x-1/2 -mt-px w-2 h-2 rotate-45 bg-popover border-r border-b border-border" />
        </div>
      )}
    </span>
  );
}
