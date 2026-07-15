import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../auth";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "./ui/dialog";
import { Button } from "./ui/button";
import { cn } from "../lib/utils";
import {
  Bot,
  ShieldCheck,
  Database,
  Sparkles,
  MessageSquare,
  Bell,
  ArrowRight,
  Check,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface OnboardingStep {
  icon: LucideIcon;
  titleKey: string;
  descKey: string;
}

const ADMIN_STEPS: OnboardingStep[] = [
  { icon: Bot, titleKey: "onboarding.stepAgentManage", descKey: "onboarding.stepAgentManageDesc" },
  { icon: ShieldCheck, titleKey: "onboarding.stepApproval", descKey: "onboarding.stepApprovalDesc" },
  { icon: Sparkles, titleKey: "onboarding.stepSkills", descKey: "onboarding.stepSkillsDesc" },
  { icon: Database, titleKey: "onboarding.stepMcp", descKey: "onboarding.stepMcpDesc" },
  { icon: Bell, titleKey: "onboarding.stepNotifications", descKey: "onboarding.stepNotificationsDesc" },
];

const EMPLOYEE_STEPS: OnboardingStep[] = [
  { icon: MessageSquare, titleKey: "onboarding.stepChat", descKey: "onboarding.stepChatDesc" },
  { icon: Sparkles, titleKey: "onboarding.stepSkillLib", descKey: "onboarding.stepSkillLibDesc" },
  { icon: Database, titleKey: "onboarding.stepKnowledge", descKey: "onboarding.stepKnowledgeDesc" },
  { icon: Bell, titleKey: "onboarding.stepNotifications", descKey: "onboarding.stepNotificationsDesc" },
];

const STORAGE_KEY = "eaos:onboarded:v1";

/**
 * First-login onboarding modal. Shows once per browser (per role group),
 * marks completion in localStorage. Rendered once near the app root.
 */
export function OnboardingGuide() {
  const { t } = useTranslation();
  const { user, isAuthenticated } = useAuth();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!isAuthenticated || !user) return;
    const completed = localStorage.getItem(STORAGE_KEY);
    if (completed) return;
    // Slight delay so the app shell settles before the modal opens.
    const id = window.setTimeout(() => setOpen(true), 400);
    return () => window.clearTimeout(id);
  }, [isAuthenticated, user]);

  const steps = user?.role === "admin" ? ADMIN_STEPS : EMPLOYEE_STEPS;
  const isAdmin = user?.role === "admin";
  const total = steps.length;
  const current = steps[step];

  const handleNext = () => {
    if (step < total - 1) {
      setStep(step + 1);
    } else {
      handleFinish();
    }
  };

  const handleSkip = () => {
    localStorage.setItem(STORAGE_KEY, "1");
    setOpen(false);
  };

  const handleFinish = () => {
    localStorage.setItem(STORAGE_KEY, "1");
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && handleSkip()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/10 text-accent">
              <Sparkles className="h-4 w-4" />
            </span>
            {isAdmin ? t("onboarding.adminWelcome") : t("onboarding.employeeWelcome")}
          </DialogTitle>
          <DialogDescription>
            {isAdmin ? t("onboarding.adminDesc") : t("onboarding.employeeDesc")}
          </DialogDescription>
        </DialogHeader>

        <div className="mt-4 flex flex-col items-center gap-4 py-2">
          <div
            key={step}
            className={cn(
              "flex h-16 w-16 items-center justify-center rounded-full",
              "bg-accent/10 text-accent",
            )}
          >
            <current.icon className="h-7 w-7" strokeWidth={1.5} />
          </div>
          <div className="text-center">
            <h3 className="text-base font-semibold text-foreground">{t(current.titleKey)}</h3>
            <p className="mt-1.5 text-sm text-secondary">{t(current.descKey)}</p>
          </div>
        </div>

        {/* Step indicators */}
        <div className="flex items-center justify-center gap-1.5 py-1">
          {steps.map((_, i) => (
            <span
              key={i}
              className={cn(
                "h-1.5 rounded-full transition-all",
                i === step ? "w-5 bg-accent" : "w-1.5 bg-subtle",
              )}
            />
          ))}
        </div>

        <DialogFooter className="mt-2">
          <Button variant="ghost" onClick={handleSkip}>
            {t("onboarding.skip")}
          </Button>
          <Button onClick={handleNext}>
            {step < total - 1 ? (
              <>
                {t("onboarding.next")}
                <ArrowRight className="h-3.5 w-3.5" />
              </>
            ) : (
              <>
                <Check className="h-3.5 w-3.5" />
                {t("onboarding.finish")}
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
