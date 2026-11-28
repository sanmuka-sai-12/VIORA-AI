import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import type {
  FlashcardStatus,
  LearningPromptMode,
  LearningToolKind,
  PersonalLearningAttachment,
  PersonalLearningAttachmentStatus,
  PersonalLearningSessionSummary,
  PersonalLearningStudyResource,
  PersonalLearningTurn,
  PracticeQuizAttemptRecord,
  QuizDifficulty,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import { RichAcademicContent } from "../../components/common/RichAcademicContent";
import { FlashcardDeckViewer } from "../../components/learning/FlashcardDeckViewer";
import { MindMapViewer } from "../../components/learning/MindMapViewer";
import { PracticeQuizViewer } from "../../components/learning/PracticeQuizViewer";
import {
  AlertBanner,
  PageHeader,
  StatusBadge,
} from "../../components/common/UiPrimitives";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import {
  clearLearningSession,
  formatSourcePageLabel,
  loadLearningSession,
  mapSessionAttachments,
  mapSessionDetailToChatEntries,
  NEW_CHAT_SENTINEL,
  readActiveSessionHint,
  saveLearningSession,
  toActiveAttachmentIds,
  toActiveAttachmentRefs,
  writeActiveSessionHint,
  type LearningAttachmentEntry,
  type LearningChatEntry,
} from "./learningSessionState";

const MAX_ATTACHMENTS = 5;

const STARTER_PROMPTS: ReadonlyArray<{
  label: string;
  prompt: string;
  mode: LearningPromptMode;
}> = [
  {
    label: "Calculus: Chain Rule",
    prompt:
      "Explain the chain rule in calculus with a step-by-step derivative example.",
    mode: "explain",
  },
  {
    label: "Algorithms: Binary Search",
    prompt:
      "Explain why binary search runs in O(log n) time and show clean Python code.",
    mode: "explain",
  },
  {
    label: "Algebra: Quadratic Formula",
    prompt: "How is the quadratic formula derived from completing the square?",
    mode: "question",
  },
  {
    label: "Physics: Newton's Second Law",
    prompt:
      "Explain Newton's second law F = ma with units and a worked force example.",
    mode: "explain",
  },
];

function attachmentTone(
  status: PersonalLearningAttachmentStatus,
): "success" | "warning" | "danger" {
  if (status === "ready") return "success";
  if (status === "partial") return "warning";
  return "danger";
}

function attachmentStatusLabel(status: PersonalLearningAttachmentStatus): string {
  if (status === "ready") return "Ready";
  if (status === "partial") return "Partial";
  return "Could Not Read";
}

function attachmentKindLabel(att: PersonalLearningAttachment): string {
  if (att.kind === "pdf") return "PDF";
  if (att.kind === "image") return "Image (OCR)";
  return "Text";
}

function formatResourceScope(
  scope: PersonalLearningStudyResource["source_scope"],
): string {
  if (scope === "materials") return "From Materials";
  if (scope === "conversation") return "From Conversation";
  if (scope === "combined") return "Topic + Materials";
  return "Selected Topic";
}

function formatShortDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function StudentLearningPage() {
  const { apiClient, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();
  const userId = auth.user?.user_id;
  const tenantId = tenant.selectedTenantId;
  const feedback = useActionFeedback();

  const initial = loadLearningSession(tenantController, userId, tenantId);
  const [sessionId, setSessionId] = useState<string | null>(
    () => initial?.sessionId ?? null,
  );
  const [sessionTitle, setSessionTitle] = useState<string>(
    () => initial?.sessionTitle ?? "New Study Session",
  );
  const [mode, setMode] = useState<LearningPromptMode>(
    () => initial?.mode ?? "question",
  );
  const [prompt, setPrompt] = useState(() => initial?.prompt ?? "");
  const [messages, setMessages] = useState<LearningChatEntry[]>(
    () => initial?.messages ?? [],
  );
  const [attachments, setAttachments] = useState<LearningAttachmentEntry[]>(
    () => initial?.attachments ?? [],
  );
  const [resources, setResources] = useState<PersonalLearningStudyResource[]>(
    () => initial?.resources ?? [],
  );

  const [sessions, setSessions] = useState<PersonalLearningSessionSummary[]>([]);
  const [library, setLibrary] = useState<PersonalLearningAttachment[]>([]);
  const [railTab, setRailTab] = useState<"history" | "library">("history");
  const [workspaceTab, setWorkspaceTab] = useState<"chat" | "resources">("chat");
  const [loadingHistory, setLoadingHistory] = useState(false);

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const [busy, setBusy] = useState(false);
  const [generatingTool, setGeneratingTool] = useState<LearningToolKind | null>(
    null,
  );
  const [toolTopic, setToolTopic] = useState("");
  const [quizDifficulty, setQuizDifficulty] = useState<QuizDifficulty>("medium");
  const [copiedResourceId, setCopiedResourceId] = useState<string | null>(null);
  const [uploadingLabel, setUploadingLabel] = useState<string | null>(null);
  const [showNoteDrawer, setShowNoteDrawer] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");

  const nextIdRef = useRef((initial?.messages.length ?? 0) + 1);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const loadedScopeRef = useRef<string | null>(
    userId && tenantId ? `${userId}:${tenantId}` : null,
  );

  const openSession = useCallback(
    async (targetSessionId: string) => {
      if (!tenantId || !userId) return;
      const res = await apiClient.GET(
        "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
        {
          params: {
            path: { tenant_id: tenantId, session_id: targetSessionId },
          },
        },
      );
      if (res.data) {
        setSessionId(res.data.session_id);
        setSessionTitle(res.data.title);
        setMode(res.data.mode);
        setMessages(mapSessionDetailToChatEntries(res.data));
        setAttachments(mapSessionAttachments(res.data.attachments));
        setResources(res.data.resources ?? []);
        setWorkspaceTab("chat");
        setPrompt("");
        setRenamingId(null);
        writeActiveSessionHint(userId, tenantId, res.data.session_id);
      }
    },
    [apiClient, tenantId, userId],
  );

  const refreshSidebarLists = useCallback(
    async (hydrateIfCold: boolean) => {
      if (!tenantId || !userId) return;
      setLoadingHistory(true);
      try {
        const [sessRes, libRes] = await Promise.all([
          apiClient.GET("/api/v1/tenants/{tenant_id}/learning/sessions", {
            params: { path: { tenant_id: tenantId }, query: { limit: 40 } },
          }),
          apiClient.GET("/api/v1/tenants/{tenant_id}/learning/materials", {
            params: { path: { tenant_id: tenantId }, query: { limit: 50 } },
          }),
        ]);
        const nextSessions = sessRes.data?.items ?? [];
        const nextLibrary = libRes.data?.items ?? [];
        setSessions(nextSessions);
        setLibrary(nextLibrary);

        if (hydrateIfCold && nextSessions.length > 0) {
          const hint = readActiveSessionHint(userId, tenantId);
          if (hint !== NEW_CHAT_SENTINEL) {
            const match = hint
              ? nextSessions.find((s) => s.session_id === hint)
              : nextSessions[0];
            if (match) {
              await openSession(match.session_id);
            }
          }
        }
      } finally {
        setLoadingHistory(false);
      }
    },
    [apiClient, openSession, tenantId, userId],
  );

  // Initial load + scope change reload (navigating back retains cache; refresh/login hydrates from DB).
  useEffect(() => {
    if (!tenantId || !userId) return;
    const scopeKey = `${userId}:${tenantId}`;
    const scopeChanged = scopeKey !== loadedScopeRef.current;
    loadedScopeRef.current = scopeKey;

    const cached = loadLearningSession(tenantController, userId, tenantId);
    if (scopeChanged) {
      setSessionId(cached?.sessionId ?? null);
      setSessionTitle(cached?.sessionTitle ?? "New Study Session");
      setMode(cached?.mode ?? "question");
      setPrompt(cached?.prompt ?? "");
      setMessages(cached?.messages ?? []);
      setAttachments(cached?.attachments ?? []);
      setResources(cached?.resources ?? []);
      nextIdRef.current = (cached?.messages.length ?? 0) + 1;
    }

    const coldWorkspace =
      !cached ||
      (cached.sessionId === null &&
        cached.messages.length === 0 &&
        cached.resources.length === 0);
    void refreshSidebarLists(coldWorkspace);
  }, [refreshSidebarLists, tenantController, tenantId, userId]);

  // Keep instant route-switch cache updated scoped cleanly to (userId, tenantId).
  useEffect(() => {
    if (!userId || !tenantId) return;
    saveLearningSession(tenantController, {
      userId,
      tenantId,
      sessionId,
      sessionTitle,
      mode,
      prompt,
      messages,
      attachments,
      resources,
    });
  }, [
    attachments,
    messages,
    mode,
    prompt,
    resources,
    sessionId,
    sessionTitle,
    tenantController,
    tenantId,
    userId,
  ]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [messages.length, busy]);

  if (!tenantId || !userId) return null;

  const syncSessionAttachments = async (nextList: LearningAttachmentEntry[]) => {
    if (!sessionId) return;
    const nextIds = toActiveAttachmentIds(nextList);
    await apiClient.PATCH(
      "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
      {
        params: { path: { tenant_id: tenantId, session_id: sessionId } },
        body: { attachment_ids: nextIds },
      },
    );
    void refreshSidebarLists(false);
  };

  const buildHistory = (entries: LearningChatEntry[]): PersonalLearningTurn[] =>
    entries.slice(-10).map((entry) => ({
      role: entry.role,
      content: entry.content,
    }));

  const activeAttachmentRefs = toActiveAttachmentRefs(attachments);
  const activeAttachmentIds = toActiveAttachmentIds(attachments);
  const activeTitles = attachments
    .filter((item) => item.included && item.attachment.status !== "unreadable")
    .slice(0, MAX_ATTACHMENTS)
    .map((item) => item.attachment.title);

  const submitPrompt = async (
    rawPrompt: string,
    selectedMode: LearningPromptMode,
  ) => {
    const trimmed = rawPrompt.trim();
    if (!trimmed || busy || uploadingLabel) return;

    const historyPayload = buildHistory(messages);
    const userEntry: LearningChatEntry = {
      id: `msg-${nextIdRef.current++}`,
      role: "user",
      content: trimmed,
      mode: selectedMode,
      attachedTitles: activeTitles.length > 0 ? activeTitles : undefined,
    };

    setMessages((prev) => [...prev, userEntry]);
    setPrompt("");

    const ok = await feedback.runApiAction({
      setBusy,
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/learning/ask", {
          params: { path: { tenant_id: tenantId } },
          body: {
            prompt: trimmed,
            mode: selectedMode,
            session_id: sessionId,
            history: historyPayload,
            attachments: activeAttachmentRefs,
            attachment_ids: activeAttachmentIds,
          },
        }),
      fallbackError: "Unable to get a response right now. Please try again.",
      onSuccess: (data) => {
        if (data.session_id) {
          setSessionId(data.session_id);
          if (data.session_title) {
            setSessionTitle(data.session_title);
          }
          writeActiveSessionHint(userId, tenantId, data.session_id);
        }
        setMessages((prev) => [
          ...prev,
          {
            id: `msg-${nextIdRef.current++}`,
            role: "assistant",
            content: data.answer,
            mode: data.mode,
            followUps: data.follow_up_prompts,
            sources: data.sources,
            truncated: data.truncated,
          },
        ]);
        void refreshSidebarLists(false);
      },
    });

    if (!ok) {
      setMessages((prev) => prev.filter((item) => item.id !== userEntry.id));
      setPrompt(trimmed);
    }
  };

  const attachMaterialToChat = (att: PersonalLearningAttachment) => {
    setAttachments((prev) => {
      if (prev.some((p) => p.attachment.attachment_id === att.attachment_id)) {
        return prev;
      }
      if (prev.length >= MAX_ATTACHMENTS) {
        return prev;
      }
      const next = [
        ...prev,
        { attachment: att, included: att.status !== "unreadable" },
      ];
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = "";
    if (selected.length === 0) return;

    const remainingSlots = MAX_ATTACHMENTS - attachments.length;
    if (remainingSlots <= 0) return;
    const queue = selected.slice(0, remainingSlots);

    for (const file of queue) {
      setUploadingLabel(`Processing ${file.name} (extracting text / OCR)...`);
      const formData = new FormData();
      formData.append("file", file);
      formData.append("title", file.name);
      await feedback.runApiAction({
        call: () =>
          apiClient.POST(
            "/api/v1/tenants/{tenant_id}/learning/attachments/file",
            {
              params: { path: { tenant_id: tenantId } },
              body: formData as never,
            },
          ),
        fallbackError: `Could not process ${file.name}. Ensure it is a readable PDF, PNG, JPEG, or text file.`,
        onSuccess: (data) => {
          attachMaterialToChat(data);
          void refreshSidebarLists(false);
        },
      });
    }
    setUploadingLabel(null);
  };

  const handleAddTextNote = async () => {
    const cleanContent = noteContent.trim();
    if (!cleanContent || uploadingLabel || attachments.length >= MAX_ATTACHMENTS) {
      return;
    }
    const cleanTitle = noteTitle.trim() || "Study Notes";
    setUploadingLabel(`Processing "${cleanTitle}"...`);
    const ok = await feedback.runApiAction({
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/learning/attachments/text",
          {
            params: { path: { tenant_id: tenantId } },
            body: {
              title: cleanTitle,
              content: cleanContent,
            },
          },
        ),
      fallbackError: "Could not process text note. Ensure at least 3 characters.",
      onSuccess: (data) => {
        attachMaterialToChat(data);
        void refreshSidebarLists(false);
      },
    });
    setUploadingLabel(null);
    if (ok) {
      setNoteTitle("");
      setNoteContent("");
      setShowNoteDrawer(false);
    }
  };

  const handleToggleAttachment = (attachmentId: string) => {
    setAttachments((prev) => {
      const next = prev.map((item) =>
        item.attachment.attachment_id === attachmentId &&
        item.attachment.status !== "unreadable"
          ? { ...item, included: !item.included }
          : item,
      );
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleRemoveAttachment = (attachmentId: string) => {
    setAttachments((prev) => {
      const next = prev.filter(
        (item) => item.attachment.attachment_id !== attachmentId,
      );
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleDeleteMaterial = async (attachmentId: string) => {
    await feedback.runApiAction({
      call: () =>
        apiClient.DELETE(
          "/api/v1/tenants/{tenant_id}/learning/materials/{attachment_id}",
          {
            params: { path: { tenant_id: tenantId, attachment_id: attachmentId } },
          },
        ),
      fallbackError: "Could not remove material from library.",
      onSuccess: () => {
        setAttachments((prev) =>
          prev.filter((item) => item.attachment.attachment_id !== attachmentId),
        );
        setLibrary((prev) =>
          prev.filter((item) => item.attachment_id !== attachmentId),
        );
        void refreshSidebarLists(false);
      },
    });
  };

  const handleNewConversation = () => {
    feedback.clearFeedback();
    setSessionId(null);
    setSessionTitle("New Study Session");
    setMessages([]);
    setAttachments([]);
    setResources([]);
    setWorkspaceTab("chat");
    setToolTopic("");
    setPrompt("");
    setShowNoteDrawer(false);
    setNoteTitle("");
    setNoteContent("");
    setRenamingId(null);
    writeActiveSessionHint(userId, tenantId, NEW_CHAT_SENTINEL);
    clearLearningSession(tenantController, userId, tenantId);
  };

  const canGenerateResource = Boolean(
    toolTopic.trim() ||
      prompt.trim() ||
      messages.length > 0 ||
      activeAttachmentRefs.length > 0,
  );

  const handleGenerateResource = async (
    toolKind: LearningToolKind,
    options?: { replaceResourceId?: string; overrideTopic?: string | null },
  ) => {
    if (busy || Boolean(generatingTool) || Boolean(uploadingLabel)) return;
    const focusTopic = (
      options?.overrideTopic ??
      (toolTopic.trim() || prompt.trim())
    ).trim();

    if (
      !focusTopic &&
      messages.length === 0 &&
      activeAttachmentRefs.length === 0
    ) {
      return;
    }

    setGeneratingTool(toolKind);
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/learning/resources/generate", {
          params: { path: { tenant_id: tenantId } },
          body: {
            tool_kind: toolKind,
            session_id: sessionId,
            topic: focusTopic || null,
            difficulty: quizDifficulty,
            include_conversation: messages.length > 0,
            history: buildHistory(messages),
            attachments: activeAttachmentRefs,
            attachment_ids: activeAttachmentIds,
            replace_resource_id: options?.replaceResourceId ?? null,
          },
        }),
      fallbackError:
        toolKind === "study_notes"
          ? "Could not generate Study Notes right now."
          : toolKind === "academic_insights"
            ? "Could not generate Academic Insights right now."
            : toolKind === "mind_map"
              ? "Could not generate a complete Mind Map right now. Try narrowing the focus topic."
              : toolKind === "flashcards"
                ? "Could not generate Flashcards right now. Try narrowing the focus topic."
                : "Could not generate Practice Quiz right now. Try narrowing the focus topic.",
      onSuccess: (created) => {
        setSessionId(created.session_id);
        if (created.session_title) {
          setSessionTitle(created.session_title);
        }
        writeActiveSessionHint(userId, tenantId, created.session_id);
        setResources((prev) => {
          const filtered = prev.filter(
            (r) => r.resource_id !== created.resource_id,
          );
          return [created, ...filtered];
        });
        setWorkspaceTab("resources");
        void refreshSidebarLists(false);
      },
    });
    setGeneratingTool(null);
  };

  const handleUpdateFlashcards = async (
    resourceId: string,
    statuses: Record<string, FlashcardStatus>,
  ) => {
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/learning/resources/{resource_id}/flashcards",
          {
            params: { path: { tenant_id: tenantId, resource_id: resourceId } },
            body: { statuses },
          },
        ),
      fallbackError: "Could not save flashcard progress.",
      onSuccess: (updated) => {
        setResources((prev) =>
          prev.map((r) => (r.resource_id === resourceId ? updated : r)),
        );
      },
    });
  };

  const handleSubmitQuiz = async (
    resourceId: string,
    answers: Record<string, number>,
  ): Promise<PracticeQuizAttemptRecord | null> => {
    let attemptOut: PracticeQuizAttemptRecord | null = null;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/learning/resources/{resource_id}/quiz/submit",
          {
            params: { path: { tenant_id: tenantId, resource_id: resourceId } },
            body: { answers },
          },
        ),
      fallbackError: "Could not score quiz attempt.",
      onSuccess: (attempt) => {
        attemptOut = attempt;
        setResources((prev) =>
          prev.map((r) => {
            if (r.resource_id !== resourceId || !r.practice_quiz) return r;
            return {
              ...r,
              practice_quiz: {
                ...r.practice_quiz,
                attempts: [attempt, ...r.practice_quiz.attempts],
              },
            };
          }),
        );
      },
    });
    return attemptOut;
  };

  const handleCopyResource = async (resource: PersonalLearningStudyResource) => {
    const header = `${resource.title}\n\n`;
    try {
      await navigator.clipboard.writeText(`${header}${resource.content}`);
      setCopiedResourceId(resource.resource_id);
      window.setTimeout(() => setCopiedResourceId(null), 1500);
    } catch {
      // Clipboard may be restricted outside secure contexts.
    }
  };

  const handleDeleteResource = async (resourceId: string) => {
    await feedback.runApiAction({
      call: () =>
        apiClient.DELETE(
          "/api/v1/tenants/{tenant_id}/learning/resources/{resource_id}",
          {
            params: { path: { tenant_id: tenantId, resource_id: resourceId } },
          },
        ),
      fallbackError: "Could not delete study resource.",
      onSuccess: () => {
        setResources((prev) =>
          prev.filter((r) => r.resource_id !== resourceId),
        );
      },
    });
  };

  const handleSaveRename = async (targetSessionId: string) => {
    const clean = renameDraft.trim();
    if (!clean) return;
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
          {
            params: { path: { tenant_id: tenantId, session_id: targetSessionId } },
            body: { title: clean },
          },
        ),
      fallbackError: "Could not rename study session.",
      onSuccess: (updated) => {
        if (sessionId === targetSessionId) {
          setSessionTitle(updated.title);
        }
        setRenamingId(null);
        setRenameDraft("");
        void refreshSidebarLists(false);
      },
    });
  };

  const handleDeleteSession = async (targetSessionId: string) => {
    await feedback.runApiAction({
      call: () =>
        apiClient.DELETE(
          "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
          {
            params: { path: { tenant_id: tenantId, session_id: targetSessionId } },
          },
        ),
      fallbackError: "Could not delete study session.",
      onSuccess: () => {
        if (sessionId === targetSessionId) {
          handleNewConversation();
        }
        void refreshSidebarLists(false);
      },
    });
  };

  const handleFormSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submitPrompt(prompt, mode);
  };

  const handleComposerKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submitPrompt(prompt, mode);
    }
  };

  const latestAssistantId = [...messages]
    .reverse()
    .find((m) => m.role === "assistant")?.id;

  return (
    <div className="viora-learning-layout">
      <aside className="viora-learning-rail" aria-label="Study History and Personal Library">
        <button
          type="button"
          className="viora-btn viora-btn-primary"
          disabled={busy || Boolean(uploadingLabel)}
          onClick={handleNewConversation}
        >
          + New Study Session
        </button>

        <div className="viora-rail-tabs" role="tablist" aria-label="Workspace panels">
          <button
            type="button"
            role="tab"
            aria-selected={railTab === "history"}
            className={`viora-rail-tab${railTab === "history" ? " is-active" : ""}`}
            onClick={() => setRailTab("history")}
          >
            History ({sessions.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={railTab === "library"}
            className={`viora-rail-tab${railTab === "library" ? " is-active" : ""}`}
            onClick={() => setRailTab("library")}
          >
            Library ({library.length})
          </button>
        </div>

        {railTab === "history" && (
          <div className="viora-rail-list">
            {loadingHistory && sessions.length === 0 && (
              <p className="viora-field-hint">Loading recent sessions...</p>
            )}
            {!loadingHistory && sessions.length === 0 && (
              <p className="viora-field-hint">
                Your saved study conversations will appear here.
              </p>
            )}
            {sessions.map((item) => {
              const selected = item.session_id === sessionId;
              const isEditing = renamingId === item.session_id;
              return (
                <div
                  key={item.session_id}
                  className={`viora-rail-item${selected ? " is-selected" : ""}`}
                >
                  {isEditing ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem" }}>
                      <input
                        type="text"
                        className="viora-input"
                        aria-label="Session title"
                        value={renameDraft}
                        onChange={(e) => setRenameDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            void handleSaveRename(item.session_id);
                          }
                        }}
                      />
                      <div className="viora-rail-actions">
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm viora-btn-primary"
                          onClick={() => void handleSaveRename(item.session_id)}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() => {
                            setRenamingId(null);
                            setRenameDraft("");
                          }}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="viora-rail-item-title"
                        disabled={busy}
                        onClick={() => void openSession(item.session_id)}
                      >
                        {item.title}
                      </button>
                      <div className="viora-rail-meta">
                        <span>{item.message_count} messages</span>
                        <span>{formatShortDate(item.updated_at)}</span>
                      </div>
                      <div className="viora-rail-actions">
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          disabled={busy}
                          onClick={() => {
                            setRenamingId(item.session_id);
                            setRenameDraft(item.title);
                          }}
                        >
                          Rename
                        </button>
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          disabled={busy}
                          onClick={() => void handleDeleteSession(item.session_id)}
                        >
                          Delete
                        </button>
                      </div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {railTab === "library" && (
          <div className="viora-rail-list">
            {library.length === 0 && (
              <p className="viora-field-hint">
                Attach a PDF, image, or study note below to build your personal study library.
              </p>
            )}
            {library.map((mat) => {
              const alreadyAttached = attachments.some(
                (a) => a.attachment.attachment_id === mat.attachment_id,
              );
              const unusable = mat.status === "unreadable";
              return (
                <div key={mat.attachment_id} className="viora-rail-item">
                  <div style={{ fontWeight: 600, fontSize: "0.83rem", wordBreak: "break-word" }}>
                    {mat.title}
                  </div>
                  <div className="viora-row" style={{ gap: "0.3rem", flexWrap: "wrap" }}>
                    <StatusBadge tone="neutral">{attachmentKindLabel(mat)}</StatusBadge>
                    <StatusBadge tone={attachmentTone(mat.status)}>
                      {attachmentStatusLabel(mat.status)}
                    </StatusBadge>
                  </div>
                  <span className="viora-field-hint">{mat.status_message}</span>
                  <div className="viora-rail-actions">
                    {!unusable && (
                      <button
                        type="button"
                        className={`viora-btn viora-btn-sm ${alreadyAttached ? "" : "viora-btn-primary"}`}
                        disabled={
                          busy ||
                          alreadyAttached ||
                          attachments.length >= MAX_ATTACHMENTS
                        }
                        onClick={() => attachMaterialToChat(mat)}
                      >
                        {alreadyAttached ? "Attached" : "Use in Chat"}
                      </button>
                    )}
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy}
                      onClick={() => void handleDeleteMaterial(mat.attachment_id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </aside>

      <div className="viora-learning-shell">
        <PageHeader
          title={sessionId ? sessionTitle : "Personal Learning"}
          subtitle="Ask questions, explore topics, or study from your private PDFs, images, and saved notes."
          actions={
            sessionId ? (
              <div className="viora-row">
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  disabled={busy}
                  onClick={() => {
                    setRailTab("history");
                    setRenamingId(sessionId);
                    setRenameDraft(sessionTitle);
                  }}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  disabled={busy || Boolean(uploadingLabel)}
                  onClick={handleNewConversation}
                >
                  New Session
                </button>
              </div>
            ) : undefined
          }
        />

        {feedback.alerts}

        <div className="viora-workspace-bar">
          <div className="viora-workspace-tabs" role="tablist" aria-label="Session view">
            <button
              type="button"
              role="tab"
              aria-selected={workspaceTab === "chat"}
              className={`viora-rail-tab${workspaceTab === "chat" ? " is-active" : ""}`}
              onClick={() => setWorkspaceTab("chat")}
            >
              Conversation
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={workspaceTab === "resources"}
              className={`viora-rail-tab${workspaceTab === "resources" ? " is-active" : ""}`}
              onClick={() => setWorkspaceTab("resources")}
            >
              Study Tools ({resources.length})
            </button>
          </div>

          <div className="viora-tools-actions">
            <input
              type="text"
              className="viora-input"
              aria-label="Focus topic for Study Notes or Insights"
              placeholder="Optional focus topic..."
              style={{ width: "190px", padding: "0.32rem 0.6rem", fontSize: "0.8rem" }}
              value={toolTopic}
              onChange={(e) => setToolTopic(e.target.value)}
              disabled={busy || Boolean(generatingTool) || Boolean(uploadingLabel)}
            />
            <button
              type="button"
              className="viora-btn viora-btn-sm viora-btn-primary"
              disabled={
                !canGenerateResource ||
                busy ||
                Boolean(generatingTool) ||
                Boolean(uploadingLabel)
              }
              title={
                canGenerateResource
                  ? "Generate structured Study Notes from your topic, conversation, or attached materials"
                  : "Enter a topic, ask a question, or attach material first"
              }
              onClick={() => void handleGenerateResource("study_notes")}
            >
              {generatingTool === "study_notes"
                ? "Creating Notes..."
                : "Study Notes"}
            </button>
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={
                !canGenerateResource ||
                busy ||
                Boolean(generatingTool) ||
                Boolean(uploadingLabel)
              }
              title={
                canGenerateResource
                  ? "Generate high-yield Academic Insights from your topic, conversation, or attached materials"
                  : "Enter a topic, ask a question, or attach material first"
              }
              onClick={() => void handleGenerateResource("academic_insights")}
            >
              {generatingTool === "academic_insights"
                ? "Analyzing..."
                : "Academic Insights"}
            </button>
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={
                !canGenerateResource ||
                busy ||
                Boolean(generatingTool) ||
                Boolean(uploadingLabel)
              }
              title={
                canGenerateResource
                  ? "Generate an interactive concept Mind Map from your topic, conversation, or attached materials"
                  : "Enter a topic, ask a question, or attach material first"
              }
              onClick={() => void handleGenerateResource("mind_map")}
            >
              {generatingTool === "mind_map" ? "Mapping..." : "Mind Map"}
            </button>
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={
                !canGenerateResource ||
                busy ||
                Boolean(generatingTool) ||
                Boolean(uploadingLabel)
              }
              title={
                canGenerateResource
                  ? "Generate interactive revision Flashcards from your topic, conversation, or attached materials"
                  : "Enter a topic, ask a question, or attach material first"
              }
              onClick={() => void handleGenerateResource("flashcards")}
            >
              {generatingTool === "flashcards" ? "Building..." : "Flashcards"}
            </button>
            <select
              aria-label="Practice quiz difficulty"
              className="viora-select"
              style={{ width: "auto", padding: "0.28rem 0.5rem", fontSize: "0.78rem" }}
              value={quizDifficulty}
              disabled={busy || Boolean(generatingTool) || Boolean(uploadingLabel)}
              onChange={(e) => setQuizDifficulty(e.target.value as QuizDifficulty)}
            >
              <option value="easy">Easy Quiz</option>
              <option value="medium">Medium Quiz</option>
              <option value="hard">Hard Quiz</option>
            </select>
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              disabled={
                !canGenerateResource ||
                busy ||
                Boolean(generatingTool) ||
                Boolean(uploadingLabel)
              }
              title={
                canGenerateResource
                  ? "Generate a multiple-choice Practice Quiz from your topic, conversation, or attached materials"
                  : "Enter a topic, ask a question, or attach material first"
              }
              onClick={() => void handleGenerateResource("practice_quiz")}
            >
              {generatingTool === "practice_quiz" ? "Creating Quiz..." : "Practice Quiz"}
            </button>
          </div>
        </div>

        {generatingTool && (
          <div className="viora-msg-assistant" role="status">
            <div className="viora-typing-dots">
              <span className="viora-typing-dot" aria-hidden="true" />
              <span className="viora-typing-dot" aria-hidden="true" />
              <span className="viora-typing-dot" aria-hidden="true" />
              <span>
                {generatingTool === "study_notes"
                  ? "Generating structured Study Notes (concepts, definitions, formulas, summary)..."
                  : generatingTool === "academic_insights"
                    ? "Generating Academic Insights (key takeaways, pitfalls, exam points)..."
                    : generatingTool === "mind_map"
                      ? "Building interactive concept Mind Map..."
                      : generatingTool === "flashcards"
                        ? "Creating interactive revision Flashcards..."
                        : `Creating ${quizDifficulty} Practice Quiz...`}
              </span>
            </div>
          </div>
        )}

        {workspaceTab === "resources" && (
          <div className="viora-resource-list">
            {resources.length === 0 && !generatingTool && (
              <section className="viora-learning-hero">
                <h2 style={{ fontSize: "1.05rem", color: "var(--color-brand-primary)" }}>
                  No Study Notes or Insights generated in this session yet
                </h2>
                <p
                  style={{
                    fontSize: "0.86rem",
                    color: "var(--color-text-muted)",
                    marginTop: "0.3rem",
                  }}
                >
                  Use <strong>Study Notes</strong> or <strong>Academic Insights</strong> above when you want structured concepts, definitions, formulas, and exam summaries saved with this session.
                </p>
              </section>
            )}

            {resources.map((res) => (
              <article key={res.resource_id} className="viora-resource-card">
                <div className="viora-resource-head">
                  <div>
                    <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--color-brand-primary)" }}>
                      {res.title}
                    </h3>
                    <div className="viora-row" style={{ marginTop: "0.3rem", flexWrap: "wrap" }}>
                      <StatusBadge
                        tone={
                          res.tool_kind === "study_notes" ||
                          res.tool_kind === "mind_map" ||
                          res.tool_kind === "flashcards" ||
                          res.tool_kind === "practice_quiz"
                            ? "brand"
                            : "neutral"
                        }
                      >
                        {res.tool_kind === "study_notes"
                          ? "Study Notes"
                          : res.tool_kind === "academic_insights"
                            ? "Academic Insights"
                            : res.tool_kind === "mind_map"
                              ? "Mind Map"
                              : res.tool_kind === "flashcards"
                                ? "Flashcards"
                                : "Practice Quiz"}
                      </StatusBadge>
                      <StatusBadge tone="neutral">
                        {formatResourceScope(res.source_scope)}
                      </StatusBadge>
                      {res.truncated && (
                        <StatusBadge tone="warning">
                          Incomplete Output (Length Limit)
                        </StatusBadge>
                      )}
                      <span className="viora-field-hint">
                        {formatShortDate(res.updated_at)}
                      </span>
                    </div>
                  </div>

                  <div className="viora-row">
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy || Boolean(generatingTool)}
                      onClick={() => void handleCopyResource(res)}
                    >
                      {copiedResourceId === res.resource_id ? "Copied" : "Copy"}
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy || Boolean(generatingTool)}
                      onClick={() =>
                        void handleGenerateResource(res.tool_kind, {
                          replaceResourceId: res.resource_id,
                          overrideTopic: res.focus_topic,
                        })
                      }
                    >
                      Regenerate
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy || Boolean(generatingTool)}
                      onClick={() => void handleDeleteResource(res.resource_id)}
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {res.truncated && (
                  <AlertBanner
                    tone="warning"
                    title="Generated output reached length limit"
                    message="This resource stopped before finishing cleanly. Narrow the focus topic or click Regenerate."
                  />
                )}

                {res.tool_kind === "mind_map" && res.mind_map ? (
                  <MindMapViewer graph={res.mind_map} />
                ) : res.tool_kind === "flashcards" && res.flashcards ? (
                  <FlashcardDeckViewer
                    deck={res.flashcards}
                    busy={busy || Boolean(generatingTool)}
                    onUpdateStatuses={(statuses) =>
                      handleUpdateFlashcards(res.resource_id, statuses)
                    }
                  />
                ) : res.tool_kind === "practice_quiz" && res.practice_quiz ? (
                  <PracticeQuizViewer
                    quiz={res.practice_quiz}
                    busy={busy || Boolean(generatingTool)}
                    onSubmitAnswers={(answers) =>
                      handleSubmitQuiz(res.resource_id, answers)
                    }
                  />
                ) : (
                  <RichAcademicContent content={res.content} />
                )}

                {res.sources && res.sources.length > 0 && (
                  <div className="viora-sources-box">
                    <span
                      style={{
                        fontSize: "0.75rem",
                        fontWeight: 600,
                        color: "var(--color-text-muted)",
                      }}
                    >
                      Preserved Material References
                    </span>
                    <div className="viora-sources-list">
                      {res.sources.map((src, idx) => (
                        <span
                          key={`${src.attachment_id}-${src.page_start}-${idx}`}
                          className="viora-source-pill"
                          title={src.excerpt}
                        >
                          <strong>{src.title}</strong>
                          <span>• {formatSourcePageLabel(src)}</span>
                          {src.section_title && <span>• {src.section_title}</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}

        {workspaceTab === "chat" && messages.length === 0 && (
          <section className="viora-learning-hero">
            <div className="viora-row" style={{ justifyContent: "space-between" }}>
              <div>
                <h2
                  style={{
                    fontSize: "1.15rem",
                    fontWeight: 700,
                    color: "var(--color-brand-primary)",
                  }}
                >
                  What would you like to understand today?
                </h2>
                <p
                  style={{
                    fontSize: "0.88rem",
                    color: "var(--color-text-muted)",
                    marginTop: "0.25rem",
                  }}
                >
                  Start a new conversation, resume a saved session from History, or attach materials from your Personal Library.
                </p>
              </div>
              <StatusBadge tone="brand">Independent Study</StatusBadge>
            </div>

            <div className="viora-starter-grid">
              {STARTER_PROMPTS.map((starter) => (
                <button
                  key={starter.label}
                  type="button"
                  className="viora-starter-btn"
                  disabled={busy || Boolean(uploadingLabel)}
                  onClick={() => {
                    setMode(starter.mode);
                    void submitPrompt(starter.prompt, starter.mode);
                  }}
                >
                  <div
                    style={{
                      fontWeight: 600,
                      color: "var(--color-brand-primary)",
                      marginBottom: "0.2rem",
                    }}
                  >
                    {starter.label}
                  </div>
                  <div style={{ color: "var(--color-text-muted)", fontSize: "0.8rem" }}>
                    {starter.prompt}
                  </div>
                </button>
              ))}
            </div>
          </section>
        )}

        {workspaceTab === "chat" && messages.length > 0 && (
          <div className="viora-chat-thread" aria-live="polite">
            {messages.map((msg) => {
              if (msg.role === "user") {
                return (
                  <div key={msg.id} className="viora-msg-user">
                    <div>{msg.content}</div>
                    {msg.attachedTitles && msg.attachedTitles.length > 0 && (
                      <div
                        style={{
                          marginTop: "0.4rem",
                          fontSize: "0.75rem",
                          opacity: 0.88,
                        }}
                      >
                        Using: {msg.attachedTitles.join(", ")}
                      </div>
                    )}
                  </div>
                );
              }

              const isLatest = msg.id === latestAssistantId;
              return (
                <article key={msg.id} className="viora-msg-assistant">
                  <div className="viora-msg-assistant-head">
                    <span>VIORA Learning Assistant</span>
                    {msg.truncated ? (
                      <StatusBadge tone="warning">
                        Incomplete Response (Length Limit)
                      </StatusBadge>
                    ) : (
                      <StatusBadge tone="neutral">
                        {msg.mode === "explain" ? "Topic Explanation" : "Direct Answer"}
                      </StatusBadge>
                    )}
                  </div>

                  {msg.truncated && (
                    <div style={{ marginBottom: "0.75rem" }}>
                      <AlertBanner
                        tone="warning"
                        title="Response cut off before finishing"
                        message="This reply reached the response length limit and is incomplete. Use Continue below to read the remaining steps."
                      />
                    </div>
                  )}

                  <RichAcademicContent content={msg.content} />

                  {msg.sources && msg.sources.length > 0 && (
                    <div className="viora-sources-box">
                      <span
                        style={{
                          fontSize: "0.75rem",
                          fontWeight: 600,
                          color: "var(--color-text-muted)",
                        }}
                      >
                        Referenced Study Materials
                      </span>
                      <div className="viora-sources-list">
                        {msg.sources.map((src, srcIdx) => (
                          <span
                            key={`${src.attachment_id}-${src.page_start}-${srcIdx}`}
                            className="viora-source-pill"
                            title={src.excerpt}
                          >
                            <strong>{src.title}</strong>
                            <span>• {formatSourcePageLabel(src)}</span>
                            {src.section_title && <span>• {src.section_title}</span>}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {isLatest && msg.followUps && msg.followUps.length > 0 && (
                    <div className="viora-followup-row">
                      {msg.followUps.map((followUp) => (
                        <button
                          key={followUp}
                          type="button"
                          className="viora-followup-chip"
                          disabled={busy || Boolean(uploadingLabel)}
                          onClick={() => void submitPrompt(followUp, "question")}
                        >
                          {followUp}
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}

            {busy && (
              <div className="viora-msg-assistant" role="status">
                <div className="viora-typing-dots">
                  <span className="viora-typing-dot" aria-hidden="true" />
                  <span className="viora-typing-dot" aria-hidden="true" />
                  <span className="viora-typing-dot" aria-hidden="true" />
                  <span>
                    {mode === "explain"
                      ? "Preparing a clear topic explanation..."
                      : "Working through your question..."}
                  </span>
                </div>
              </div>
            )}

            <div ref={threadEndRef} />
          </div>
        )}

        <form onSubmit={handleFormSubmit} className="viora-composer">
          <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <div className="viora-row" role="group" aria-label="Learning mode">
              <button
                type="button"
                className={`viora-btn viora-btn-sm ${mode === "question" ? "viora-btn-primary" : ""}`}
                disabled={busy}
                onClick={() => setMode("question")}
              >
                Ask a Question
              </button>
              <button
                type="button"
                className={`viora-btn viora-btn-sm ${mode === "explain" ? "viora-btn-primary" : ""}`}
                disabled={busy}
                onClick={() => setMode("explain")}
              >
                Explain a Topic
              </button>
            </div>
            <div className="viora-row">
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.txt,.md"
                multiple
                style={{ display: "none" }}
                onChange={(e) => void handleFileChange(e)}
              />
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={
                  busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS
                }
                onClick={() => fileInputRef.current?.click()}
              >
                Attach PDF / Image / Text
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={
                  busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS
                }
                onClick={() => setShowNoteDrawer((prev) => !prev)}
              >
                {showNoteDrawer ? "Close Note" : "Paste Study Note"}
              </button>
            </div>
          </div>

          {uploadingLabel && (
            <div className="viora-typing-dots" role="status">
              <span className="viora-typing-dot" aria-hidden="true" />
              <span className="viora-typing-dot" aria-hidden="true" />
              <span className="viora-typing-dot" aria-hidden="true" />
              <span>{uploadingLabel}</span>
            </div>
          )}

          {showNoteDrawer && (
            <div className="viora-text-note-drawer">
              <input
                type="text"
                className="viora-input"
                aria-label="Study note title"
                placeholder="Note title (optional, e.g. Thermodynamics Ch. 3)"
                value={noteTitle}
                onChange={(e) => setNoteTitle(e.target.value)}
                disabled={Boolean(uploadingLabel)}
              />
              <textarea
                className="viora-input"
                aria-label="Study note text"
                rows={3}
                placeholder="Paste excerpt, definitions, or lecture notes here..."
                value={noteContent}
                onChange={(e) => setNoteContent(e.target.value)}
                disabled={Boolean(uploadingLabel)}
              />
              <div className="viora-row" style={{ justifyContent: "flex-end" }}>
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  onClick={() => setShowNoteDrawer(false)}
                  disabled={Boolean(uploadingLabel)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="viora-btn viora-btn-sm viora-btn-primary"
                  disabled={Boolean(uploadingLabel) || noteContent.trim().length < 3}
                  onClick={() => void handleAddTextNote()}
                >
                  Process Note
                </button>
              </div>
            </div>
          )}

          {attachments.length > 0 && (
            <div className="viora-attachment-tray" aria-label="Attached study materials">
              {attachments.map(({ attachment, included }) => {
                const unusable = attachment.status === "unreadable";
                return (
                  <div
                    key={attachment.attachment_id}
                    className={`viora-attachment-chip${!included || unusable ? " is-excluded" : ""}`}
                  >
                    <div
                      style={{
                        display: "flex",
                        flexDirection: "column",
                        gap: "0.15rem",
                        minWidth: 0,
                      }}
                    >
                      <div className="viora-row" style={{ flexWrap: "wrap", gap: "0.4rem" }}>
                        <strong
                          style={{
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            maxWidth: "280px",
                          }}
                        >
                          {attachment.title}
                        </strong>
                        <StatusBadge tone="neutral">
                          {attachmentKindLabel(attachment)}
                        </StatusBadge>
                        <StatusBadge tone={attachmentTone(attachment.status)}>
                          {attachmentStatusLabel(attachment.status)}
                        </StatusBadge>
                      </div>
                      <span
                        style={{
                          fontSize: "0.75rem",
                          color: "var(--color-text-muted)",
                        }}
                      >
                        {attachment.status_message}
                      </span>
                    </div>

                    <div className="viora-row">
                      {!unusable && (
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          disabled={busy}
                          onClick={() =>
                            handleToggleAttachment(attachment.attachment_id)
                          }
                        >
                          {included ? "Active" : "Paused"}
                        </button>
                      )}
                      <button
                        type="button"
                        className="viora-btn viora-btn-sm"
                        disabled={busy}
                        aria-label={`Detach ${attachment.title}`}
                        onClick={() =>
                          handleRemoveAttachment(attachment.attachment_id)
                        }
                      >
                        Detach
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <textarea
            id="personal-learning-input"
            aria-label="Academic question or topic"
            className="viora-input"
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder={
              activeTitles.length > 0
                ? `Ask a question about ${activeTitles.join(", ")}...`
                : mode === "explain"
                  ? "Enter a topic you want explained clearly (e.g. eigenvalues, recursion, photosynthesis)..."
                  : messages.length > 0
                    ? "Ask a follow-up question or change the topic..."
                    : "Ask any academic question across math, science, programming, or theory..."
            }
            disabled={busy}
            required
          />

          <div className="viora-row" style={{ justifyContent: "space-between" }}>
            <span className="viora-field-hint">
              Enter to send • Shift+Enter for new line
              {attachments.length > 0
                ? ` • ${activeAttachmentRefs.length}/${attachments.length} material${attachments.length === 1 ? "" : "s"} active`
                : ""}
            </span>
            <button
              type="submit"
              className="viora-btn viora-btn-primary"
              disabled={busy || Boolean(uploadingLabel) || !prompt.trim()}
            >
              {busy
                ? "Generating..."
                : mode === "explain"
                  ? "Explain Topic"
                  : messages.length > 0
                    ? "Send Follow-up"
                    : "Ask Question"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
