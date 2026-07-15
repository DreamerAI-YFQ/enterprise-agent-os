import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Image, FileText, Paperclip, Send, Square, X } from "lucide-react";
import { Button, type AttachmentRef, cn, toast, uploadFile } from "@eaos/shared";
import { apiClient } from "@eaos/shared/api";

interface ChatInputProps {
  onSend: (text: string, attachments?: AttachmentRef[]) => void;
  onCancel: () => void;
  isStreaming: boolean;
  disabled?: boolean;
}

interface Skill {
  id: string;
  name: string;
  display_name: string;
  description: string;
  scope: string;
  status: string;
}

const SCOPE_LABELS: Record<string, string> = {
  personal: "个人",
  department: "部门",
  company: "公司",
};

const SCOPE_COLORS: Record<string, string> = {
  personal: "bg-accent-subtle text-accent",
  department: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  company: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
};

const ACCEPTED_TYPES =
  "image/jpeg,image/png,image/webp,image/gif,application/pdf,text/plain,text/markdown,text/csv";

const ACCEPTED_SET = new Set(ACCEPTED_TYPES.split(","));
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB

export function ChatInput({
  onSend,
  onCancel,
  isStreaming,
  disabled,
}: ChatInputProps) {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<AttachmentRef[]>([]);
  const [uploading, setUploading] = useState(false);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropRef = useRef<HTMLDivElement>(null);

  // Load user-visible skills for @mention autocomplete
  const { data: skills } = useQuery({
    queryKey: ["skills", "mention"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/skills", {});
      if (error || !data) return [] as Skill[];
      return data as unknown as Skill[];
    },
  });

  // Filter skills by mention query
  const filteredSkills = useMemo(() => {
    if (!skills || mentionQuery === null) return [];
    const published = skills.filter((s) => s.status === "published");
    if (mentionQuery === "") return published.slice(0, 8);
    const q = mentionQuery.toLowerCase();
    return published
      .filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.display_name.toLowerCase().includes(q),
      )
      .slice(0, 8);
  }, [skills, mentionQuery]);

  // Auto-resize the textarea to fit content (capped at 200px).
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`;
  }, [value]);

  // Detect @mention trigger in textarea
  const detectMention = useCallback(
    (text: string, cursorPos: number) => {
      // Find the last @ before the cursor
      const beforeCursor = text.slice(0, cursorPos);
      const atMatch = beforeCursor.match(/@(\w*)$/);
      if (atMatch) {
        setMentionQuery(atMatch[1]);
        setMentionIndex(0);
      } else {
        setMentionQuery(null);
      }
    },
    [],
  );

  const insertMention = (skillName: string) => {
    const ta = textareaRef.current;
    if (!ta) return;
    const cursorPos = ta.selectionStart;
    const beforeCursor = value.slice(0, cursorPos);
    const afterCursor = value.slice(cursorPos);
    // Replace @partial with @skill_name
    const newValue = beforeCursor.replace(/@(\w*)$/, `@${skillName} `) + afterCursor;
    setValue(newValue);
    setMentionQuery(null);
    // Focus and move cursor after the inserted mention
    requestAnimationFrame(() => {
      ta.focus();
      const newPos = beforeCursor.replace(/@(\w*)$/, `@${skillName} `).length;
      ta.setSelectionRange(newPos, newPos);
    });
  };

  const handleFiles = useCallback(
    async (files: FileList | File[]) => {
      const arr = Array.from(files);
      const valid: File[] = [];
      for (const f of arr) {
        if (!ACCEPTED_SET.has(f.type)) {
          toast.show({
            title: `不支持的文件类型：${f.name}`,
            description: "仅支持图片(JPEG/PNG/WebP/GIF)、PDF、文本、Markdown、CSV",
            variant: "danger",
          });
          continue;
        }
        if (f.size > MAX_FILE_SIZE) {
          toast.show({
            title: `文件过大：${f.name}`,
            description: `最大 10MB，当前 ${(f.size / 1024 / 1024).toFixed(1)}MB`,
            variant: "danger",
          });
          continue;
        }
        valid.push(f);
      }
      if (valid.length === 0) return;
      setUploading(true);
      try {
        const results = await Promise.all(valid.map((f) => uploadFile(f)));
        setAttachments((prev) => [...prev, ...results]);
      } catch (err) {
        const msg = err instanceof Error ? err.message : "上传失败";
        toast.show({ title: msg, variant: "danger" });
      } finally {
        setUploading(false);
      }
    },
    [],
  );

  const removeAttachment = (fileId: string) => {
    setAttachments((prev) => prev.filter((a) => a.file_id !== fileId));
  };

  const submit = () => {
    const trimmed = value.trim();
    if ((!trimmed && attachments.length === 0) || isStreaming || disabled) return;
    onSend(trimmed || "请查看附件", attachments.length > 0 ? attachments : undefined);
    setValue("");
    setMentionQuery(null);
    setAttachments([]);
  };

  // Drag-and-drop handlers
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.files.length > 0) {
      void handleFiles(e.dataTransfer.files);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // @mention keyboard navigation
    if (mentionQuery !== null && filteredSkills.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((prev) => (prev + 1) % filteredSkills.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((prev) => (prev - 1 + filteredSkills.length) % filteredSkills.length);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const selected = filteredSkills[mentionIndex];
        if (selected) {
          insertMention(selected.name);
          return;
        }
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMentionQuery(null);
        return;
      }
    }

    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div
      ref={dropRef}
      className={`border-t border-border bg-elevated px-6 py-4 ${
        uploading ? "opacity-70" : ""
      }`}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      {/* Attachment chips */}
      {attachments.length > 0 && (
        <div className="mx-auto mb-2 flex max-w-3xl flex-wrap gap-1.5">
          {attachments.map((att) => (
            <span
              key={att.file_id}
              className="inline-flex items-center gap-1 rounded-md border border-border bg-subtle px-2 py-1 text-xs text-foreground"
            >
              {att.type === "image" ? (
                <Image className="h-3 w-3 text-accent" />
              ) : (
                <FileText className="h-3 w-3 text-secondary" />
              )}
              <span className="max-w-[120px] truncate">{att.name}</span>
              <button
                type="button"
                onClick={() => removeAttachment(att.file_id)}
                className="ml-0.5 text-tertiary hover:text-danger"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="mx-auto flex max-w-3xl items-end gap-2">
        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept={ACCEPTED_TYPES}
          className="hidden"
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              void handleFiles(e.target.files);
            }
            e.target.value = "";
          }}
        />

        {/* Paperclip button */}
        <Button
          variant="ghost"
          size="icon"
          onClick={() => fileInputRef.current?.click()}
          disabled={disabled || uploading || isStreaming}
          aria-label="添加附件"
        >
          <Paperclip className="h-4 w-4" />
        </Button>

        {/* Textarea + mention popup container */}
        <div className="relative flex-1">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              detectMention(e.target.value, e.target.selectionStart);
            }}
            onKeyDown={handleKeyDown}
            onBlur={() => {
              // Delay to allow click on mention item
              setTimeout(() => setMentionQuery(null), 150);
            }}
            rows={1}
            placeholder="输入消息，Enter 发送，Shift+Enter 换行，@ 触发技能"
            className="w-full resize-none rounded-md border border-border bg-subtle px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:opacity-50"
            disabled={disabled || uploading}
          />

          {/* @mention autocomplete popup */}
          {mentionQuery !== null && filteredSkills.length > 0 && (
            <div className="absolute bottom-full left-0 mb-1 w-full overflow-hidden rounded-lg border border-border bg-elevated shadow-lg">
              <div className="border-b border-border-subtle px-3 py-1.5 text-xs text-tertiary">
                技能列表（↑↓ 选择，Enter 确认，Esc 取消）
              </div>
              <div className="max-h-60 overflow-y-auto">
                {filteredSkills.map((skill, idx) => (
                  <button
                    key={skill.id}
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      insertMention(skill.name);
                    }}
                    onMouseEnter={() => setMentionIndex(idx)}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2 text-left transition-colors",
                      idx === mentionIndex
                        ? "bg-accent-subtle"
                        : "hover:bg-subtle",
                    )}
                  >
                    <span className="font-mono text-xs text-accent">
                      @{skill.name}
                    </span>
                    <span className="text-sm text-foreground">
                      {skill.display_name}
                    </span>
                    <span
                      className={cn(
                        "ml-auto rounded-full px-1.5 py-0.5 text-xs",
                        SCOPE_COLORS[skill.scope] ?? "bg-subtle text-tertiary",
                      )}
                    >
                      {SCOPE_LABELS[skill.scope] ?? skill.scope}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {isStreaming ? (
          <Button
            variant="outline"
            size="icon"
            onClick={onCancel}
            aria-label="停止生成"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            size="icon"
            onClick={submit}
            disabled={(!value.trim() && attachments.length === 0) || disabled || uploading}
            aria-label="发送"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
