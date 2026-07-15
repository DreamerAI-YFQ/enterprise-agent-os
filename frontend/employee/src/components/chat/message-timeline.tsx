import { type AgentEvent } from "@eaos/shared";
import { EventRenderer } from "./event-renderers";

interface TimelineStep {
  title: string | null;
  events: AgentEvent[];
}

const STEP_BODY_TYPES = new Set([
  "plan",
  "reason",
  "tool_call",
  "tool_result",
  "reflect",
]);

/**
 * Group a flat list of AgentEvents into steps.
 * A `step` event opens a new step; subsequent plan/reason/tool_call/
 * tool_result/reflect events belong to that step until the next step.
 */
function groupBySteps(events: AgentEvent[]): TimelineStep[] {
  const steps: TimelineStep[] = [];
  let current: TimelineStep | null = null;
  for (const event of events) {
    if (event.type === "step") {
      if (current) steps.push(current);
      current = { title: event.content, events: [] };
    } else if (STEP_BODY_TYPES.has(event.type)) {
      if (!current) current = { title: null, events: [] };
      current.events.push(event);
    }
  }
  if (current) steps.push(current);
  return steps;
}

/**
 * F1-T7 — Plan-Execute-Reflect vertical timeline.
 * Walks the assistant events and renders each step as a timeline node
 * with a vertical connecting line.
 */
export function MessageTimeline({ events }: { events: AgentEvent[] }) {
  const steps = groupBySteps(events);

  if (steps.length === 0) {
    // No step markers — render misc events flatly (if any).
    const misc = events.filter((e) =>
      STEP_BODY_TYPES.has(e.type) || e.type === "error"
    );
    if (misc.length === 0) return null;
    return (
      <div className="mt-3 space-y-1.5">
        {misc.map((e, i) => (
          <EventRenderer key={i} event={e} />
        ))}
      </div>
    );
  }

  return (
    <div className="mt-3 space-y-3">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1;
        return (
          <div key={i} className="relative pl-5">
            {/* Vertical connector */}
            {!isLast && (
              <span
                className="absolute left-[7px] top-4 bottom-0 w-px bg-border-subtle"
                aria-hidden
              />
            )}
            {/* Node dot */}
            <span
              className="absolute left-0 top-1.5 h-3.5 w-3.5 rounded-full border-2 border-accent bg-elevated"
              aria-hidden
            />
            {step.title && (
              <div className="mb-1.5 text-xs font-semibold text-foreground">
                {step.title}
              </div>
            )}
            <div className="space-y-1.5">
              {step.events.map((e, j) => (
                <EventRenderer key={j} event={e} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
