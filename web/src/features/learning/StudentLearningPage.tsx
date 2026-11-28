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
  AuthenticatedUser,
  FlashcardStatus,
  InstitutionDiscovery,
  LearningPromptMode,
  LearningToolKind,
  PersonalLearningAttachment,
  PersonalLearningAttachmentStatus,
  PersonalLearningSessionSummary,
  PersonalLearningStudyResource,
  PersonalLearningTurn,
  PracticeExamActiveAttempt,
  PracticeExamAttemptRecord,
  PracticeQuizAttemptRecord,
  QuizDifficulty,
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AppRouteId } from "../../auth/routeGuard";
import { RichAcademicContent } from "../../components/common/RichAcademicContent";
import { StatusBadge } from "../../components/common/UiPrimitives";
import { FlashcardDeckViewer } from "../../components/learning/FlashcardDeckViewer";
import {
  ChatBubbleIcon,
  ChevronsRightIcon,
  DotsHorizontalIcon,
  HomeIcon,
  InstitutionIcon,
  LibraryIcon,
  PlusIcon,
  SendArrowIcon,
  SidebarToggleIcon,
  StudioSparkIcon,
} from "../../components/learning/LearningIcons";
import { MindMapViewer } from "../../components/learning/MindMapViewer";
import { PracticeExamViewer } from "../../components/learning/PracticeExamViewer";
import { PracticeQuizViewer } from "../../components/learning/PracticeQuizViewer";
import { useTenant } from "../../tenant/TenantContext";
import { useActionFeedback } from "../common/useActionFeedback";
import {
  clearLearningSession,
  consumeClassroomMaterialHandoff,
  formatSourcePageLabel,
  loadLearningSession,
  mapSessionAttachments,
  mapSessionDetailToChatEntries,
  NEW_CHAT_SENTINEL,
  readActiveSessionHint,
  resolveInitialSessionIdToOpen,
  saveLearningSession,
  toActiveAttachmentIds,
  toActiveAttachmentRefs,
  writeActiveSessionHint,
  type LearningAttachmentEntry,
  type LearningChatEntry,
} from "./learningSessionState";

const MAX_ATTACHMENTS = 5;

export interface StudentLearningPageProps {
  user?: AuthenticatedUser | null;
  institutions?: InstitutionDiscovery[];
  selectedInstitution?: InstitutionDiscovery | null;
  onNavigate?: (route: AppRouteId) => void;
  onSelectInstitution?: (tenantId: string) => void;
  onLogout?: () => void;
  authBusy?: boolean;
}

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

function resourceKindLabel(kind: LearningToolKind): string {
  if (kind === "study_notes") return "Notes";
  if (kind === "academic_insights") return "Insights";
  if (kind === "mind_map") return "Mind Map";
  if (kind === "flashcards") return "Flashcards";
  if (kind === "practice_exam") return "Practice Exam";
  return "Practice Quiz";
}

function resourceMetaSubtitle(res: PersonalLearningStudyResource): string {
  const date = formatShortDate(res.updated_at) || "Today";
  if (res.tool_kind === "flashcards" && res.flashcards) {
    return `${res.flashcards.cards.length} cards · ${date}`;
  }
  if (res.tool_kind === "mind_map" && res.mind_map) {
    return `${res.mind_map.nodes.length} concepts · ${date}`;
  }
  if (res.tool_kind === "practice_quiz" && res.practice_quiz) {
    return `${res.practice_quiz.questions.length} Qs · ${date}`;
  }
  if (res.tool_kind === "practice_exam" && res.practice_exam) {
    return `${res.practice_exam.question_count} Qs · ${res.practice_exam.duration_minutes}m · ${date}`;
  }
  return `${resourceKindLabel(res.tool_kind)} · ${date}`;
}

export function StudentLearningPage({
  user,
  institutions = [],
  selectedInstitution = null,
  onNavigate,
  onSelectInstitution,
  onLogout,
  authBusy = false,
}: StudentLearningPageProps = {}) {
  const { apiClient, snapshot: auth } = useAuth();
  const { snapshot: tenant, controller: tenantController } = useTenant();
  const effectiveUser = user ?? auth.user;
  const effectiveInst = selectedInstitution ?? tenant.selectedInstitution;
  const userId = effectiveUser?.user_id;
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
  const [activeSection, setActiveSection] = useState<"home" | "library">("home");
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [studioOpen, setStudioOpen] = useState(true);
  const [materialsTrayOpen, setMaterialsTrayOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [focusedResourceId, setFocusedResourceId] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const [busy, setBusy] = useState(false);
  const [generatingTool, setGeneratingTool] = useState<LearningToolKind | null>(
    null,
  );
  const [toolTopic, setToolTopic] = useState("");
  const [quizDifficulty, setQuizDifficulty] = useState<QuizDifficulty>("medium");
  const [examQuestionCount, setExamQuestionCount] = useState<number>(6);
  const [examDurationMinutes, setExamDurationMinutes] = useState<number>(15);
  const [copiedResourceId, setCopiedResourceId] = useState<string | null>(null);
  const [copiedMsgId, setCopiedMsgId] = useState<string | null>(null);
  const [uploadingLabel, setUploadingLabel] = useState<string | null>(null);
  const [showNoteDrawer, setShowNoteDrawer] = useState(false);
  const [noteTitle, setNoteTitle] = useState("");
  const [noteContent, setNoteContent] = useState("");

  const nextIdRef = useRef((initial?.messages.length ?? 0) + 1);
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
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
        const loadedResources = res.data.resources ?? [];
        setSessionId(res.data.session_id);
        setSessionTitle(res.data.title);
        setMode(res.data.mode);
        setMessages(mapSessionDetailToChatEntries(res.data));
        setAttachments(mapSessionAttachments(res.data.attachments));
        setResources(loadedResources);
        setActiveSection("home");
        setMobileNavOpen(false);
        setMaterialsTrayOpen(false);
        setPrompt("");
        setRenamingId(null);
        const activeExam = loadedResources.find(
          (r) =>
            r.tool_kind === "practice_exam" &&
            r.practice_exam?.active_attempt != null,
        );
        setFocusedResourceId(activeExam ? activeExam.resource_id : null);
        writeActiveSessionHint(userId, tenantId, res.data.session_id);
      }
    },
    [apiClient, tenantId, userId],
  );

  const refreshSidebarLists = useCallback(
    async (hydrateIfExplicitHint: boolean) => {
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

        // Fresh logins have hint === null and must NEVER open the latest chat automatically.
        // Only restore when an explicit session was opened in this browser tab session.
        if (hydrateIfExplicitHint && nextSessions.length > 0) {
          const explicitId = resolveInitialSessionIdToOpen(
            readActiveSessionHint(userId, tenantId),
            nextSessions,
          );
          if (explicitId) {
            await openSession(explicitId);
          }
        }
      } finally {
        setLoadingHistory(false);
      }
    },
    [apiClient, openSession, tenantId, userId],
  );

  // Initial load + scope change reload.
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
      setFocusedResourceId(null);
      nextIdRef.current = (cached?.messages.length ?? 0) + 1;
    }

    const coldWorkspace =
      !cached ||
      (cached.sessionId === null &&
        cached.messages.length === 0 &&
        cached.resources.length === 0);

    const handoff = consumeClassroomMaterialHandoff(userId, tenantId);
    if (handoff) {
      setSessionId(null);
      setSessionTitle(handoff.classroomTitle ?? handoff.attachment.title);
      setMode("question");
      setMessages([]);
      setAttachments([{ attachment: handoff.attachment, included: true }]);
      setResources([]);
      setFocusedResourceId(null);
      setActiveSection("home");
      setPrompt(`Explain the key concepts from "${handoff.attachment.title}".`);
      void refreshSidebarLists(false);
    } else {
      void refreshSidebarLists(coldWorkspace);
    }
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

    setActiveSection("home");
    setFocusedResourceId(null);
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
      fallbackError:
        "Could not generate an explanation right now. Please try again.",
      onSuccess: (data) => {
        if (data.session_id) {
          setSessionId(data.session_id);
          writeActiveSessionHint(userId, tenantId, data.session_id);
        }
        if (data.session_title) {
          setSessionTitle(data.session_title);
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

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submitPrompt(prompt, mode);
  };

  const handleComposerKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submitPrompt(prompt, mode);
    }
  };

  const handleNewChat = () => {
    setSessionId(null);
    setSessionTitle("New Study Session");
    setMode("question");
    setPrompt("");
    setMessages([]);
    setAttachments([]);
    setResources([]);
    setFocusedResourceId(null);
    setActiveSection("home");
    setMaterialsTrayOpen(false);
    setMobileNavOpen(false);
    setRenamingId(null);
    feedback.setActionError(null);
    clearLearningSession(tenantController, userId, tenantId);
    writeActiveSessionHint(userId, tenantId, NEW_CHAT_SENTINEL);
  };

  const handleRenameSession = async (targetSessionId: string) => {
    const clean = renameDraft.trim();
    if (!clean) {
      setRenamingId(null);
      return;
    }
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/learning/sessions/{session_id}",
          {
            params: { path: { tenant_id: tenantId, session_id: targetSessionId } },
            body: { title: clean },
          },
        ),
      fallbackError: "Could not rename session.",
      onSuccess: (updated) => {
        if (sessionId === targetSessionId) {
          setSessionTitle(updated.title);
        }
        setRenamingId(null);
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
      fallbackError: "Could not delete session.",
      onSuccess: () => {
        if (sessionId === targetSessionId) {
          handleNewChat();
        }
        void refreshSidebarLists(false);
      },
    });
  };

  const handleUseLibraryItem = (item: PersonalLearningAttachment) => {
    if (item.status === "unreadable") return;
    setActiveSection("home");
    setMobileNavOpen(false);
    setAttachments((prev) => {
      const exists = prev.some(
        (entry) => entry.attachment.attachment_id === item.attachment_id,
      );
      if (exists) {
        const next = prev.map((entry) =>
          entry.attachment.attachment_id === item.attachment_id
            ? { ...entry, included: true }
            : entry,
        );
        void syncSessionAttachments(next);
        return next;
      }
      if (prev.length >= MAX_ATTACHMENTS) {
        feedback.setActionError(null);
        return prev;
      }
      const next = [...prev, { attachment: item, included: true }];
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleDeleteLibraryItem = async (attachmentId: string) => {
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
        void refreshSidebarLists(false);
      },
    });
  };

  const handleFileSelected = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;

    if (attachments.length >= MAX_ATTACHMENTS) {
      return;
    }

    setUploadingLabel(
      file.type.startsWith("image/")
        ? `Reading text from ${file.name}...`
        : `Extracting text from ${file.name}...`,
    );

    const formData = new FormData();
    formData.append("file", file);

    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/learning/attachments/file", {
          params: { path: { tenant_id: tenantId } },
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          body: formData as any,
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          bodySerializer: (body: any) => body,
        }),
      fallbackError:
        "Could not process this file. Please upload a valid PDF, image, or text file.",
      onSuccess: (created) => {
        setAttachments((prev) => {
          const next = [
            ...prev.filter(
              (item) => item.attachment.attachment_id !== created.attachment_id,
            ),
            { attachment: created, included: created.status !== "unreadable" },
          ];
          void syncSessionAttachments(next);
          return next;
        });
        void refreshSidebarLists(false);
      },
    });

    setUploadingLabel(null);
  };

  const handleAddTextNote = async (e: FormEvent) => {
    e.preventDefault();
    const clean = noteContent.trim();
    if (!clean || attachments.length >= MAX_ATTACHMENTS) return;

    setUploadingLabel("Processing study text...");
    await feedback.runApiAction({
      call: () =>
        apiClient.POST("/api/v1/tenants/{tenant_id}/learning/attachments/text", {
          params: { path: { tenant_id: tenantId } },
          body: {
            title: noteTitle.trim() || null,
            content: clean,
          },
        }),
      fallbackError: "Could not process study text.",
      onSuccess: (created) => {
        setAttachments((prev) => {
          const next = [
            ...prev.filter(
              (item) => item.attachment.attachment_id !== created.attachment_id,
            ),
            { attachment: created, included: true },
          ];
          void syncSessionAttachments(next);
          return next;
        });
        setNoteTitle("");
        setNoteContent("");
        setShowNoteDrawer(false);
        void refreshSidebarLists(false);
      },
    });
    setUploadingLabel(null);
  };

  const toggleAttachment = (attachmentId: string) => {
    setAttachments((prev) => {
      const next = prev.map((item) =>
        item.attachment.attachment_id === attachmentId
          ? { ...item, included: !item.included }
          : item,
      );
      void syncSessionAttachments(next);
      return next;
    });
  };

  const removeAttachment = (attachmentId: string) => {
    setAttachments((prev) => {
      const next = prev.filter(
        (item) => item.attachment.attachment_id !== attachmentId,
      );
      void syncSessionAttachments(next);
      return next;
    });
  };

  const handleGenerateResource = async (
    toolKind: LearningToolKind,
    options?: { replaceResourceId?: string; overrideTopic?: string | null },
  ) => {
    if (busy || generatingTool || uploadingLabel) return;
    const focusTopic = (
      options?.overrideTopic !== undefined
        ? (options.overrideTopic ?? "")
        : toolTopic.trim() || prompt.trim()
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
            exam_question_count: examQuestionCount,
            exam_duration_minutes: examDurationMinutes,
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
                : toolKind === "practice_exam"
                  ? "Could not generate the complete Practice Exam right now. Try fewer questions or a narrower topic."
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
        setFocusedResourceId(created.resource_id);
        setActiveSection("home");
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

  const handleStartExam = async (
    resourceId: string,
  ): Promise<PracticeExamActiveAttempt | null> => {
    let activeOut: PracticeExamActiveAttempt | null = null;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/learning/resources/{resource_id}/exam/start",
          {
            params: { path: { tenant_id: tenantId, resource_id: resourceId } },
          },
        ),
      fallbackError: "Could not start timed practice exam.",
      onSuccess: (active) => {
        activeOut = active;
        setResources((prev) =>
          prev.map((r) => {
            if (r.resource_id !== resourceId || !r.practice_exam) return r;
            return {
              ...r,
              practice_exam: {
                ...r.practice_exam,
                active_attempt: active,
              },
            };
          }),
        );
      },
    });
    return activeOut;
  };

  const handleSaveExamProgress = async (
    resourceId: string,
    attemptId: string,
    answers: Record<string, number>,
    markedForReview: string[],
  ): Promise<PracticeExamActiveAttempt | null> => {
    let activeOut: PracticeExamActiveAttempt | null = null;
    await feedback.runApiAction({
      call: () =>
        apiClient.PATCH(
          "/api/v1/tenants/{tenant_id}/learning/resources/{resource_id}/exam/progress",
          {
            params: { path: { tenant_id: tenantId, resource_id: resourceId } },
            body: {
              attempt_id: attemptId,
              answers,
              marked_for_review: markedForReview,
            },
          },
        ),
      fallbackError: "Could not autosave exam progress.",
      onSuccess: (active) => {
        activeOut = active;
      },
    });
    return activeOut;
  };

  const handleSubmitExam = async (
    resourceId: string,
    attemptId: string,
    answers: Record<string, number>,
    autoSubmitted: boolean,
  ): Promise<PracticeExamAttemptRecord | null> => {
    let attemptOut: PracticeExamAttemptRecord | null = null;
    await feedback.runApiAction({
      call: () =>
        apiClient.POST(
          "/api/v1/tenants/{tenant_id}/learning/resources/{resource_id}/exam/submit",
          {
            params: { path: { tenant_id: tenantId, resource_id: resourceId } },
            body: {
              attempt_id: attemptId,
              answers,
              auto_submitted: autoSubmitted,
            },
          },
        ),
      fallbackError: "Could not score practice exam.",
      onSuccess: (attempt) => {
        attemptOut = attempt;
        setResources((prev) =>
          prev.map((r) => {
            if (r.resource_id !== resourceId || !r.practice_exam) return r;
            return {
              ...r,
              practice_exam: {
                ...r.practice_exam,
                active_attempt: null,
                attempts: [attempt, ...r.practice_exam.attempts],
              },
            };
          }),
        );
      },
    });
    return attemptOut;
  };

  const handleCopyResource = async (resource: PersonalLearningStudyResource) => {
    try {
      await navigator.clipboard.writeText(`${resource.title}\n\n${resource.content}`);
      setCopiedResourceId(resource.resource_id);
      window.setTimeout(() => setCopiedResourceId(null), 1500);
    } catch {
      // Clipboard may be restricted outside secure contexts.
    }
  };

  const handleCopyMessage = async (msg: LearningChatEntry) => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopiedMsgId(msg.id);
      window.setTimeout(() => setCopiedMsgId(null), 1500);
    } catch {
      // Ignore clipboard restriction.
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
        if (focusedResourceId === resourceId) {
          setFocusedResourceId(null);
        }
      },
    });
  };

  const canGenerateResource =
    Boolean(toolTopic.trim()) ||
    Boolean(prompt.trim()) ||
    messages.length > 0 ||
    activeAttachmentRefs.length > 0;

  const isHomepage =
    activeSection === "home" &&
    sessionId === null &&
    messages.length === 0 &&
    resources.length === 0 &&
    !focusedResourceId;

  const focusedResource =
    focusedResourceId !== null
      ? resources.find((r) => r.resource_id === focusedResourceId) ?? null
      : null;

  const isActiveTimedExam = Boolean(
    focusedResource?.tool_kind === "practice_exam" &&
    focusedResource.practice_exam?.active_attempt != null,
  );

  const userInitial = (effectiveUser?.display_name?.trim()?.[0] ?? "S").toUpperCase();

  const topBarTitle =
    activeSection === "library"
      ? "My Library"
      : focusedResource?.tool_kind === "practice_exam"
        ? "Practice examination"
        : focusedResource
          ? focusedResource.title
          : isHomepage
            ? "Personal Learning"
            : sessionTitle;

  const renderAttachmentChips = () => {
    if (attachments.length === 0 && !uploadingLabel) return null;
    return (
      <div className="viora-row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
        {attachments.map((entry) => {
          const att = entry.attachment;
          const unreadable = att.status === "unreadable";
          return (
            <div
              key={att.attachment_id}
              className={`viora-attachment-chip ${entry.included ? "is-active" : ""}`}
            >
              <label className="viora-attachment-toggle">
                <input
                  type="checkbox"
                  checked={entry.included && !unreadable}
                  disabled={unreadable || busy}
                  onChange={() => toggleAttachment(att.attachment_id)}
                />
                <span className="viora-attachment-name" title={att.status_message}>
                  {att.title}
                </span>
              </label>
              <span className="viora-attachment-kind">
                {attachmentKindLabel(att)}
              </span>
              <StatusBadge tone={attachmentTone(att.status)}>
                {attachmentStatusLabel(att.status)}
              </StatusBadge>
              <button
                type="button"
                className="viora-chip-remove"
                aria-label={`Remove ${att.title}`}
                disabled={busy}
                onClick={() => removeAttachment(att.attachment_id)}
              >
                ×
              </button>
            </div>
          );
        })}
        {uploadingLabel && (
          <span className="viora-field-hint" role="status">
            {uploadingLabel}
          </span>
        )}
      </div>
    );
  };

  const renderNoteDrawer = () => {
    if (!showNoteDrawer) return null;
    return (
      <form
        className="viora-note-drawer"
        onSubmit={(e) => void handleAddTextNote(e)}
        style={{ width: "100%", marginTop: "0.45rem" }}
      >
        <input
          className="viora-input"
          type="text"
          placeholder="Title (optional, e.g. Integration Notes)"
          value={noteTitle}
          onChange={(e) => setNoteTitle(e.target.value)}
          maxLength={200}
        />
        <textarea
          className="viora-textarea"
          rows={3}
          placeholder="Paste notes, definitions, problem statements, or excerpts..."
          value={noteContent}
          onChange={(e) => setNoteContent(e.target.value)}
          required
        />
        <div className="viora-row" style={{ justifyContent: "flex-end" }}>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => setShowNoteDrawer(false)}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="viora-btn viora-btn-sm viora-btn-primary"
            disabled={!noteContent.trim() || Boolean(uploadingLabel)}
          >
            Attach Text
          </button>
        </div>
      </form>
    );
  };

  return (
    <div className="viora-student-shell">
      <input
        ref={fileInputRef}
        type="file"
        accept=".pdf,.txt,.md,.csv,.png,.jpg,.jpeg,.webp,application/pdf,text/plain,image/png,image/jpeg,image/webp"
        style={{ display: "none" }}
        onChange={(e) => void handleFileSelected(e)}
      />

      {/* Single Collapsible Navigation Rail */}
      <aside
        className={`viora-nav-rail ${navCollapsed ? "is-collapsed" : ""} ${mobileNavOpen ? "is-mobile-open" : ""
          }`}
        aria-label="Personal Learning navigation"
      >
        <div className="viora-nav-header">
          {!navCollapsed && (
            <button
              type="button"
              className="viora-nav-brand"
              onClick={handleNewChat}
            >
              <span className="viora-nav-brand-badge" aria-hidden="true">
                V
              </span>
              <span>VIORA</span>
            </button>
          )}
          <button
            type="button"
            className="viora-icon-btn"
            aria-label={navCollapsed ? "Expand navigation" : "Collapse navigation"}
            title={navCollapsed ? "Expand navigation" : "Collapse navigation"}
            onClick={() => {
              if (mobileNavOpen) {
                setMobileNavOpen(false);
              } else {
                setNavCollapsed((prev) => !prev);
              }
            }}
          >
            <SidebarToggleIcon />
          </button>
        </div>

        <button
          type="button"
          className="viora-nav-new-btn"
          onClick={handleNewChat}
          title="Start a new learning session"
        >
          <PlusIcon width={16} height={16} />
          {!navCollapsed && <span>New Learning</span>}
        </button>

        <div className="viora-nav-primary">
          <button
            type="button"
            className={`viora-nav-link ${activeSection === "home" && isHomepage ? "is-active" : ""
              }`}
            onClick={handleNewChat}
            title="Home"
          >
            <HomeIcon />
            {!navCollapsed && <span>Home</span>}
          </button>
          <button
            type="button"
            className={`viora-nav-link ${activeSection === "library" ? "is-active" : ""
              }`}
            onClick={() => {
              setActiveSection("library");
              setMobileNavOpen(false);
            }}
            title="My Library"
          >
            <LibraryIcon />
            {!navCollapsed && <span>My Library</span>}
          </button>
        </div>

        {!navCollapsed && (
          <>
            <div className="viora-nav-section-label">Recent Sessions</div>
            <div className="viora-nav-sessions">
              {loadingHistory && sessions.length === 0 && (
                <p className="viora-field-hint" style={{ padding: "0 0.5rem" }}>
                  Loading sessions...
                </p>
              )}
              {!loadingHistory && sessions.length === 0 && (
                <p className="viora-field-hint" style={{ padding: "0 0.5rem" }}>
                  Your saved sessions will appear here.
                </p>
              )}
              {sessions.map((s) => {
                const isActive =
                  activeSection === "home" && s.session_id === sessionId;
                return (
                  <div
                    key={s.session_id}
                    className={`viora-nav-session-row ${isActive ? "is-active" : ""
                      }`}
                  >
                    {renamingId === s.session_id ? (
                      <form
                        style={{
                          display: "flex",
                          gap: "0.25rem",
                          width: "100%",
                          padding: "0.25rem",
                        }}
                        onSubmit={(e) => {
                          e.preventDefault();
                          void handleRenameSession(s.session_id);
                        }}
                      >
                        <input
                          className="viora-input"
                          style={{ padding: "0.25rem 0.45rem", fontSize: "0.8rem" }}
                          value={renameDraft}
                          onChange={(e) => setRenameDraft(e.target.value)}
                          autoFocus
                          maxLength={200}
                        />
                        <button
                          type="submit"
                          className="viora-btn viora-btn-sm viora-btn-primary"
                        >
                          Save
                        </button>
                      </form>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="viora-nav-session-btn"
                          onClick={() => void openSession(s.session_id)}
                          title={s.title}
                        >
                          <ChatBubbleIcon style={{ flexShrink: 0, color: "#64748b" }} />
                          <span className="viora-nav-session-title">
                            {s.title}
                          </span>
                        </button>
                        <div className="viora-nav-session-actions">
                          <button
                            type="button"
                            className="viora-icon-btn"
                            style={{ width: 24, height: 24, fontSize: "0.72rem" }}
                            title="Rename session"
                            onClick={() => {
                              setRenamingId(s.session_id);
                              setRenameDraft(s.title);
                            }}
                          >
                            ✎
                          </button>
                          <button
                            type="button"
                            className="viora-icon-btn"
                            style={{ width: 24, height: 24, fontSize: "0.82rem" }}
                            title="Delete session"
                            onClick={() => void handleDeleteSession(s.session_id)}
                          >
                            ×
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}

        <div className="viora-nav-footer">
          <button
            type="button"
            className="viora-inst-card"
            onClick={() => onNavigate?.("student-classrooms")}
            title="My Classrooms"
            style={{ marginBottom: "0.4rem" }}
          >
            <LibraryIcon style={{ flexShrink: 0, color: "#2e42a3" }} />
            {!navCollapsed && (
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: "0.82rem", fontWeight: 700, color: "#1e293b" }}>
                  My Classrooms
                </div>
                <div style={{ fontSize: "0.74rem", color: "#64748b" }}>
                  Materials & announcements
                </div>
              </div>
            )}
          </button>

          <button
            type="button"
            className="viora-inst-card"
            onClick={() => onNavigate?.("institution")}
            title={effectiveInst?.name ?? "My Institution"}
          >
            <InstitutionIcon style={{ flexShrink: 0, color: "#2e42a3" }} />
            {!navCollapsed && (
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: "0.82rem", fontWeight: 700, color: "#1e293b" }}>
                  My Institution
                </div>
                <div
                  style={{
                    fontSize: "0.74rem",
                    color: "#64748b",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {effectiveInst?.name ?? "Open Institution"}
                </div>
              </div>
            )}
          </button>

          <div className="viora-account-row">
            <div style={{ display: "flex", alignItems: "center", gap: "0.55rem", minWidth: 0 }}>
              <span className="viora-avatar-circle" aria-hidden="true">
                {userInitial}
              </span>
              {!navCollapsed && (
                <div style={{ minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: "0.82rem",
                      fontWeight: 600,
                      color: "#0f172a",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {effectiveUser?.display_name ?? "Student"}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "#64748b" }}>
                    Account &amp; settings
                  </div>
                </div>
              )}
            </div>
            {!navCollapsed && (
              <button
                type="button"
                className="viora-icon-btn"
                aria-label="Account and settings"
                aria-expanded={accountMenuOpen}
                onClick={() => setAccountMenuOpen((prev) => !prev)}
              >
                <DotsHorizontalIcon />
              </button>
            )}

            {accountMenuOpen && !navCollapsed && (
              <div className="viora-account-menu" role="menu">
                {institutions.length > 1 && onSelectInstitution && (
                  <div style={{ padding: "0.35rem 0.5rem" }}>
                    <label
                      htmlFor="student-inst-switch"
                      style={{ fontSize: "0.7rem", fontWeight: 600, color: "#64748b" }}
                    >
                      Switch Institution
                    </label>
                    <select
                      id="student-inst-switch"
                      className="viora-select"
                      style={{ marginTop: "0.2rem", fontSize: "0.78rem" }}
                      value={effectiveInst?.tenant_id ?? ""}
                      onChange={(e) => {
                        setAccountMenuOpen(false);
                        onSelectInstitution(e.target.value);
                      }}
                    >
                      {institutions.map((inst) => (
                        <option key={inst.tenant_id} value={inst.tenant_id}>
                          {inst.name}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <button
                  type="button"
                  className="viora-account-menu-item"
                  onClick={() => {
                    setAccountMenuOpen(false);
                    onNavigate?.("account-password");
                  }}
                >
                  Change Password
                </button>
                <button
                  type="button"
                  className="viora-account-menu-item is-danger"
                  disabled={authBusy}
                  onClick={() => {
                    setAccountMenuOpen(false);
                    onLogout?.();
                  }}
                >
                  {authBusy ? "Signing out..." : "Sign out"}
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>

      {/* Main Workspace */}
      <main id="main-content" className="viora-workspace-main">
        <header className="viora-workspace-topbar">
          <div style={{ display: "flex", alignItems: "center", gap: "0.65rem", minWidth: 0 }}>
            <button
              type="button"
              className="viora-btn viora-btn-sm viora-mobile-menu-btn"
              onClick={() => setMobileNavOpen((prev) => !prev)}
            >
              Menu
            </button>
            <span
              style={{
                fontSize: "0.94rem",
                fontWeight: 600,
                color: "#1e293b",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {topBarTitle}
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "0.55rem" }}>
            {activeSection === "home" && !isHomepage && !isActiveTimedExam && !studioOpen && (
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => setStudioOpen(true)}
              >
                <StudioSparkIcon /> Study Studio
              </button>
            )}
            <span className="viora-avatar-circle" title={effectiveUser?.display_name ?? "Student"}>
              {userInitial}
            </span>
          </div>
        </header>

        {feedback.actionError && (
          <div style={{ padding: "0.65rem 1.25rem 0" }}>
            {feedback.alerts}
          </div>
        )}

        {/* VIEW 1: My Library */}
        {activeSection === "library" ? (
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "1.5rem" }}>
            <div style={{ maxWidth: 860, margin: "0 auto", display: "grid", gap: "1rem" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: "0.6rem",
                }}
              >
                <div>
                  <h1 style={{ margin: 0, fontSize: "1.35rem", color: "#0f172a" }}>
                    My Library
                  </h1>
                  <p className="viora-field-hint" style={{ marginTop: "0.2rem" }}>
                    Private PDFs, images (OCR), and study notes saved to your account.
                  </p>
                </div>
                <div className="viora-row" style={{ gap: "0.5rem" }}>
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm viora-btn-primary"
                    disabled={Boolean(uploadingLabel)}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    + Upload PDF / Image
                  </button>
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm"
                    disabled={Boolean(uploadingLabel)}
                    onClick={() => setShowNoteDrawer((prev) => !prev)}
                  >
                    + Paste Text Note
                  </button>
                </div>
              </div>

              {uploadingLabel && (
                <p className="viora-field-hint" role="status">
                  {uploadingLabel}
                </p>
              )}
              {renderNoteDrawer()}

              {library.length === 0 ? (
                <div
                  style={{
                    border: "1px dashed #cbd5e1",
                    borderRadius: 12,
                    padding: "2rem",
                    textAlign: "center",
                    color: "#64748b",
                  }}
                >
                  No personal study materials yet. Upload a PDF, image, or text note to get started.
                </div>
              ) : (
                <div style={{ display: "grid", gap: "0.65rem" }}>
                  {library.map((item) => (
                    <div
                      key={item.attachment_id}
                      style={{
                        border: "1px solid #e2e8f0",
                        borderRadius: 12,
                        padding: "0.85rem 1rem",
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        gap: "0.75rem",
                        flexWrap: "wrap",
                      }}
                    >
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div style={{ fontWeight: 600, fontSize: "0.92rem", color: "#0f172a" }}>
                          {item.title}
                        </div>
                        <div className="viora-row" style={{ gap: "0.4rem", marginTop: "0.25rem", flexWrap: "wrap" }}>
                          <StatusBadge tone="neutral">{attachmentKindLabel(item)}</StatusBadge>
                          <StatusBadge tone={attachmentTone(item.status)}>
                            {attachmentStatusLabel(item.status)}
                          </StatusBadge>
                          <span className="viora-field-hint">
                            {item.page_count} page{item.page_count === 1 ? "" : "s"} ·{" "}
                            {formatShortDate(item.created_at)}
                          </span>
                        </div>
                      </div>
                      <div className="viora-row" style={{ gap: "0.45rem" }}>
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm viora-btn-primary"
                          disabled={item.status === "unreadable"}
                          onClick={() => handleUseLibraryItem(item)}
                        >
                          Use in Session
                        </button>
                        <button
                          type="button"
                          className="viora-btn viora-btn-sm"
                          onClick={() => void handleDeleteLibraryItem(item.attachment_id)}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : focusedResource ? (
          /* VIEW 2: Focused Resource / Practice Exam Mode */
          <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "1.25rem 1.5rem 2.5rem" }}>
            <div style={{ maxWidth: 920, margin: "0 auto", display: "grid", gap: "1rem" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: "0.5rem",
                }}
              >
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  onClick={() => setFocusedResourceId(null)}
                >
                  ‹ Back to learning session
                </button>

                {!isActiveTimedExam && (
                  <div className="viora-row" style={{ gap: "0.4rem" }}>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy || Boolean(generatingTool)}
                      onClick={() => void handleCopyResource(focusedResource)}
                    >
                      {copiedResourceId === focusedResource.resource_id
                        ? "Copied"
                        : "Copy"}
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy || Boolean(generatingTool)}
                      onClick={() =>
                        void handleGenerateResource(focusedResource.tool_kind, {
                          replaceResourceId: focusedResource.resource_id,
                          overrideTopic: focusedResource.focus_topic,
                        })
                      }
                    >
                      Regenerate
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      disabled={busy || Boolean(generatingTool)}
                      onClick={() => void handleDeleteResource(focusedResource.resource_id)}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>

              <div className="viora-resource-card">
                <div className="viora-resource-head">
                  <div>
                    <h2 style={{ margin: 0, fontSize: "1.25rem", color: "#0f172a" }}>
                      {focusedResource.title}
                    </h2>
                    <div className="viora-row" style={{ marginTop: "0.35rem", flexWrap: "wrap" }}>
                      <StatusBadge tone="brand">
                        {resourceKindLabel(focusedResource.tool_kind)}
                      </StatusBadge>
                      <StatusBadge tone="neutral">
                        {formatResourceScope(focusedResource.source_scope)}
                      </StatusBadge>
                      {focusedResource.truncated && (
                        <StatusBadge tone="warning">
                          Incomplete Output (Length Limit)
                        </StatusBadge>
                      )}
                    </div>
                  </div>
                </div>

                {focusedResource.tool_kind === "mind_map" && focusedResource.mind_map ? (
                  <MindMapViewer graph={focusedResource.mind_map} />
                ) : focusedResource.tool_kind === "flashcards" &&
                  focusedResource.flashcards ? (
                  <FlashcardDeckViewer
                    deck={focusedResource.flashcards}
                    busy={busy || Boolean(generatingTool)}
                    onUpdateStatuses={(statuses) =>
                      handleUpdateFlashcards(focusedResource.resource_id, statuses)
                    }
                  />
                ) : focusedResource.tool_kind === "practice_quiz" &&
                  focusedResource.practice_quiz ? (
                  <PracticeQuizViewer
                    quiz={focusedResource.practice_quiz}
                    busy={busy || Boolean(generatingTool)}
                    onSubmitAnswers={(answers) =>
                      handleSubmitQuiz(focusedResource.resource_id, answers)
                    }
                  />
                ) : focusedResource.tool_kind === "practice_exam" &&
                  focusedResource.practice_exam ? (
                  <PracticeExamViewer
                    exam={focusedResource.practice_exam}
                    busy={busy || Boolean(generatingTool)}
                    onStartExam={() => handleStartExam(focusedResource.resource_id)}
                    onSaveProgress={(attemptId, answers, markedForReview) =>
                      handleSaveExamProgress(
                        focusedResource.resource_id,
                        attemptId,
                        answers,
                        markedForReview,
                      )
                    }
                    onSubmitExam={(attemptId, answers, autoSubmitted) =>
                      handleSubmitExam(
                        focusedResource.resource_id,
                        attemptId,
                        answers,
                        autoSubmitted,
                      )
                    }
                  />
                ) : (
                  <RichAcademicContent content={focusedResource.content} />
                )}

                {focusedResource.sources && focusedResource.sources.length > 0 && (
                  <div className="viora-sources-box">
                    <span style={{ fontSize: "0.75rem", fontWeight: 600, color: "#64748b" }}>
                      Preserved Material References
                    </span>
                    <div className="viora-sources-list">
                      {focusedResource.sources.map((src, idx) => (
                        <span
                          key={`${src.attachment_id}-${src.page_start}-${idx}`}
                          className="viora-source-pill"
                          title={src.excerpt}
                        >
                          {src.title} · {formatSourcePageLabel(src)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : isHomepage ? (
          /* VIEW 3A: Fresh Login / Empty Welcoming Learning Homepage */
          <div className="viora-home-scroll">
            <div className="viora-home-container">
              <div style={{ textAlign: "center" }}>
                <div
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 12,
                    background: "#eef2ff",
                    color: "#2e42a3",
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontWeight: 800,
                    fontSize: "1.1rem",
                    marginBottom: "0.75rem",
                  }}
                >
                  V
                </div>
                <h1 className="viora-home-title">What will you learn today?</h1>
                <p className="viora-home-subtitle">
                  Explore a topic, clarify a doubt or learn from your materials.
                </p>
              </div>

              <form className="viora-composer-card" onSubmit={handleSubmit}>
                {renderAttachmentChips()}
                <textarea
                  ref={composerRef}
                  className="viora-composer-textarea"
                  placeholder="Ask anything you want to learn..."
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  disabled={busy}
                  rows={2}
                />
                {renderNoteDrawer()}
                <div className="viora-composer-bar">
                  <div className="viora-row" style={{ gap: "0.45rem", flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="viora-attach-pill"
                      disabled={busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <PlusIcon width={14} height={14} /> Attach study material
                    </button>
                    <button
                      type="button"
                      className="viora-attach-pill"
                      disabled={busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS}
                      onClick={() => setShowNoteDrawer((prev) => !prev)}
                    >
                      Paste notes
                    </button>
                    <select
                      aria-label="Response mode"
                      className="viora-select"
                      style={{
                        width: "auto",
                        padding: "0.28rem 0.55rem",
                        fontSize: "0.78rem",
                        borderRadius: 999,
                      }}
                      value={mode}
                      onChange={(e) => setMode(e.target.value as LearningPromptMode)}
                    >
                      <option value="question">Question</option>
                      <option value="explain">Explain</option>
                    </select>
                  </div>

                  <button
                    type="submit"
                    className="viora-send-circle"
                    aria-label="Send message"
                    disabled={!prompt.trim() || busy || Boolean(uploadingLabel)}
                  >
                    <SendArrowIcon width={16} height={16} />
                  </button>
                </div>
              </form>

              <div className="viora-starter-grid">
                <button
                  type="button"
                  className="viora-starter-card"
                  onClick={() => {
                    setMode("explain");
                    setPrompt("Explain ");
                    composerRef.current?.focus();
                  }}
                >
                  <div className="viora-row" style={{ gap: "0.4rem" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: "#4f46e5" }} />
                    <strong style={{ fontSize: "0.86rem", color: "#0f172a" }}>
                      Explain a topic
                    </strong>
                  </div>
                  <span style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Understand a concept
                  </span>
                </button>

                <button
                  type="button"
                  className="viora-starter-card"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <div className="viora-row" style={{ gap: "0.4rem" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: "#0d9488" }} />
                    <strong style={{ fontSize: "0.86rem", color: "#0f172a" }}>
                      Learn from notes
                    </strong>
                  </div>
                  <span style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Upload PDF or images
                  </span>
                </button>

                <button
                  type="button"
                  className="viora-starter-card"
                  onClick={() => {
                    setMode("question");
                    setPrompt("Quiz me on ");
                    composerRef.current?.focus();
                  }}
                >
                  <div className="viora-row" style={{ gap: "0.4rem" }}>
                    <span style={{ width: 8, height: 8, borderRadius: 99, background: "#d97706" }} />
                    <strong style={{ fontSize: "0.86rem", color: "#0f172a" }}>
                      Practice a subject
                    </strong>
                  </div>
                  <span style={{ fontSize: "0.78rem", color: "#64748b" }}>
                    Revise and test yourself
                  </span>
                </button>
              </div>

              {sessions.length > 0 && (
                <div style={{ width: "100%", display: "grid", gap: "0.55rem", marginTop: "0.25rem" }}>
                  <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "#475569" }}>
                    Continue learning
                  </span>
                  <div className="viora-continue-grid">
                    {sessions.slice(0, 2).map((s) => (
                      <button
                        key={s.session_id}
                        type="button"
                        className="viora-continue-card"
                        onClick={() => void openSession(s.session_id)}
                      >
                        <div style={{ minWidth: 0 }}>
                          <div
                            style={{
                              fontSize: "0.86rem",
                              fontWeight: 600,
                              color: "#0f172a",
                              overflow: "hidden",
                              textOverflow: "ellipsis",
                              whiteSpace: "nowrap",
                            }}
                          >
                            {s.title}
                          </div>
                          <div style={{ fontSize: "0.74rem", color: "#64748b" }}>
                            Resume saved session
                          </div>
                        </div>
                        <span style={{ color: "#64748b", fontSize: "0.95rem" }}>›</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          /* VIEW 3B: Active Learning Session (ChatGPT Conversation + NotebookLM Study Studio) */
          <div className="viora-workspace-body">
            <section className="viora-chat-column" aria-label="Learning conversation">
              <div className="viora-chat-subbar">
                <div className="viora-row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="viora-attach-pill"
                    onClick={() => setMaterialsTrayOpen((prev) => !prev)}
                  >
                    Materials ({attachments.length})
                  </button>
                  <button
                    type="button"
                    className="viora-attach-pill"
                    disabled={Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    + Upload
                  </button>
                </div>

                <div className="viora-row" style={{ gap: "0.4rem" }}>
                  {sessionId && (
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => {
                        setRenamingId(sessionId);
                        setRenameDraft(sessionTitle);
                      }}
                    >
                      Rename
                    </button>
                  )}
                </div>
              </div>

              {materialsTrayOpen && (
                <div
                  style={{
                    padding: "0.65rem 1.25rem",
                    borderBottom: "1px solid #e2e8f0",
                    background: "#f8fafc",
                    display: "grid",
                    gap: "0.5rem",
                  }}
                >
                  {renderAttachmentChips()}
                  <div className="viora-row" style={{ gap: "0.45rem" }}>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      Upload PDF / Image
                    </button>
                    <button
                      type="button"
                      className="viora-btn viora-btn-sm"
                      onClick={() => setShowNoteDrawer((prev) => !prev)}
                    >
                      Paste Text Note
                    </button>
                  </div>
                  {renderNoteDrawer()}
                </div>
              )}

              <div className="viora-chat-scroll">
                <div className="viora-chat-thread-inner">
                  {messages.map((msg) =>
                    msg.role === "user" ? (
                      <div key={msg.id} className="viora-user-bubble">
                        <div>{msg.content}</div>
                        {msg.attachedTitles && msg.attachedTitles.length > 0 && (
                          <div
                            style={{
                              marginTop: "0.3rem",
                              fontSize: "0.75rem",
                              color: "#475569",
                            }}
                          >
                            {msg.attachedTitles.join(" · ")}
                          </div>
                        )}
                      </div>
                    ) : (
                      <div key={msg.id} className="viora-assistant-turn">
                        <div className="viora-row" style={{ gap: "0.45rem" }}>
                          <span
                            className="viora-nav-brand-badge"
                            style={{ width: 22, height: 22, fontSize: "0.72rem", borderRadius: 99 }}
                            aria-hidden="true"
                          >
                            V
                          </span>
                          <strong style={{ fontSize: "0.8rem", color: "#334155" }}>
                            VIORA
                          </strong>
                          {msg.truncated && (
                            <StatusBadge tone="warning">Truncated</StatusBadge>
                          )}
                        </div>

                        <RichAcademicContent content={msg.content} />

                        {msg.sources && msg.sources.length > 0 && (
                          <div style={{ fontSize: "0.76rem", color: "#64748b" }}>
                            Source:{" "}
                            {msg.sources
                              .map((s) => `${s.title}, ${formatSourcePageLabel(s)}`)
                              .join(" · ")}
                          </div>
                        )}

                        <div
                          className="viora-row"
                          style={{ gap: "0.65rem", fontSize: "0.78rem", color: "#64748b", flexWrap: "wrap" }}
                        >
                          <button
                            type="button"
                            style={{
                              background: "none",
                              border: "none",
                              padding: 0,
                              color: "inherit",
                              cursor: "pointer",
                              fontWeight: 500,
                            }}
                            onClick={() => void handleCopyMessage(msg)}
                          >
                            {copiedMsgId === msg.id ? "Copied" : "Copy"}
                          </button>
                          <span>·</span>
                          <button
                            type="button"
                            style={{
                              background: "none",
                              border: "none",
                              padding: 0,
                              color: "inherit",
                              cursor: "pointer",
                              fontWeight: 500,
                            }}
                            disabled={busy}
                            onClick={() =>
                              void submitPrompt(
                                "Explain the previous answer further with a concrete example.",
                                "explain",
                              )
                            }
                          >
                            Explain further
                          </button>
                        </div>

                        {msg.followUps && msg.followUps.length > 0 && (
                          <div className="viora-row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
                            {msg.followUps.map((q) => (
                              <button
                                key={q}
                                type="button"
                                className="viora-attach-pill"
                                disabled={busy}
                                onClick={() => void submitPrompt(q, "question")}
                              >
                                {q}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ),
                  )}

                  {busy && (
                    <div className="viora-assistant-turn" role="status">
                      <div className="viora-typing-dots">
                        <span className="viora-typing-dot" aria-hidden="true" />
                        <span className="viora-typing-dot" aria-hidden="true" />
                        <span className="viora-typing-dot" aria-hidden="true" />
                        <span>Thinking...</span>
                      </div>
                    </div>
                  )}

                  <div ref={threadEndRef} />
                </div>
              </div>

              <div className="viora-chat-composer-dock">
                <form
                  className="viora-composer-card viora-chat-composer-inner"
                  onSubmit={handleSubmit}
                >
                  {!materialsTrayOpen && renderAttachmentChips()}
                  <textarea
                    ref={composerRef}
                    className="viora-composer-textarea"
                    placeholder="Ask a follow-up question..."
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    onKeyDown={handleComposerKeyDown}
                    disabled={busy}
                    rows={1}
                  />
                  <div className="viora-composer-bar">
                    <div className="viora-row" style={{ gap: "0.4rem" }}>
                      <button
                        type="button"
                        className="viora-icon-btn"
                        aria-label="Attach study material"
                        title="Attach PDF, image, or text"
                        disabled={busy || Boolean(uploadingLabel) || attachments.length >= MAX_ATTACHMENTS}
                        onClick={() => fileInputRef.current?.click()}
                      >
                        <PlusIcon width={16} height={16} />
                      </button>
                      <select
                        aria-label="Response mode"
                        className="viora-select"
                        style={{
                          width: "auto",
                          padding: "0.22rem 0.5rem",
                          fontSize: "0.76rem",
                          borderRadius: 999,
                        }}
                        value={mode}
                        onChange={(e) => setMode(e.target.value as LearningPromptMode)}
                      >
                        <option value="question">Question</option>
                        <option value="explain">Explain</option>
                      </select>
                    </div>
                    <button
                      type="submit"
                      className="viora-send-circle"
                      aria-label="Send follow-up"
                      disabled={!prompt.trim() || busy || Boolean(uploadingLabel)}
                    >
                      <SendArrowIcon width={16} height={16} />
                    </button>
                  </div>
                </form>
              </div>
            </section>

            {/* Collapsible NotebookLM-inspired Study Studio */}
            {studioOpen && (
              <aside className="viora-studio-panel" aria-label="Study Studio">
                <div className="viora-studio-header">
                  <div>
                    <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700, color: "#0f172a" }}>
                      Study Studio
                    </h2>
                    <p style={{ margin: "0.15rem 0 0", fontSize: "0.76rem", color: "#64748b" }}>
                      Create from this learning session
                    </p>
                  </div>
                  <button
                    type="button"
                    className="viora-icon-btn"
                    aria-label="Collapse Study Studio"
                    onClick={() => setStudioOpen(false)}
                  >
                    <ChevronsRightIcon />
                  </button>
                </div>

                <div className="viora-studio-scroll">
                  <div style={{ display: "grid", gap: "0.45rem" }}>
                    <input
                      className="viora-input"
                      type="text"
                      style={{ fontSize: "0.8rem", padding: "0.4rem 0.6rem" }}
                      placeholder="Optional focus topic..."
                      value={toolTopic}
                      onChange={(e) => setToolTopic(e.target.value)}
                    />
                    <div className="viora-row" style={{ gap: "0.35rem", flexWrap: "wrap" }}>
                      <select
                        aria-label="Quiz or exam difficulty"
                        className="viora-select"
                        style={{ width: "auto", padding: "0.22rem 0.45rem", fontSize: "0.75rem" }}
                        value={quizDifficulty}
                        onChange={(e) => setQuizDifficulty(e.target.value as QuizDifficulty)}
                      >
                        <option value="easy">Easy</option>
                        <option value="medium">Medium</option>
                        <option value="hard">Hard</option>
                      </select>
                      <select
                        aria-label="Practice exam question count"
                        className="viora-select"
                        style={{ width: "auto", padding: "0.22rem 0.45rem", fontSize: "0.75rem" }}
                        value={examQuestionCount}
                        onChange={(e) => setExamQuestionCount(Number(e.target.value))}
                      >
                        <option value={5}>5 Qs</option>
                        <option value={6}>6 Qs</option>
                        <option value={8}>8 Qs</option>
                        <option value={10}>10 Qs</option>
                        <option value={12}>12 Qs</option>
                        <option value={15}>15 Qs</option>
                        <option value={20}>20 Qs</option>
                      </select>
                      <select
                        aria-label="Practice exam duration"
                        className="viora-select"
                        style={{ width: "auto", padding: "0.22rem 0.45rem", fontSize: "0.75rem" }}
                        value={examDurationMinutes}
                        onChange={(e) => setExamDurationMinutes(Number(e.target.value))}
                      >
                        <option value={5}>5 min</option>
                        <option value={10}>10 min</option>
                        <option value={15}>15 min</option>
                        <option value={20}>20 min</option>
                        <option value={30}>30 min</option>
                        <option value={45}>45 min</option>
                        <option value={60}>60 min</option>
                      </select>
                    </div>
                  </div>

                  {generatingTool && (
                    <div className="viora-msg-assistant" role="status" style={{ padding: "0.55rem 0.7rem" }}>
                      <div className="viora-typing-dots">
                        <span className="viora-typing-dot" aria-hidden="true" />
                        <span className="viora-typing-dot" aria-hidden="true" />
                        <span className="viora-typing-dot" aria-hidden="true" />
                        <span style={{ fontSize: "0.8rem" }}>
                          Generating {resourceKindLabel(generatingTool)}...
                        </span>
                      </div>
                    </div>
                  )}

                  <div className="viora-studio-tool-grid">
                    <button
                      type="button"
                      className="viora-studio-tool-card viora-tool-notes"
                      disabled={!canGenerateResource || busy || Boolean(generatingTool)}
                      onClick={() => void handleGenerateResource("study_notes")}
                    >
                      <strong style={{ fontSize: "0.83rem", color: "#0f172a" }}>Study Notes</strong>
                      <span style={{ fontSize: "0.72rem", color: "#64748b" }}>Organize knowledge</span>
                    </button>

                    <button
                      type="button"
                      className="viora-studio-tool-card viora-tool-insights"
                      disabled={!canGenerateResource || busy || Boolean(generatingTool)}
                      onClick={() => void handleGenerateResource("academic_insights")}
                    >
                      <strong style={{ fontSize: "0.83rem", color: "#0f172a" }}>Insights</strong>
                      <span style={{ fontSize: "0.72rem", color: "#64748b" }}>Key takeaways</span>
                    </button>

                    <button
                      type="button"
                      className="viora-studio-tool-card viora-tool-mindmap"
                      disabled={!canGenerateResource || busy || Boolean(generatingTool)}
                      onClick={() => void handleGenerateResource("mind_map")}
                    >
                      <strong style={{ fontSize: "0.83rem", color: "#0f172a" }}>Mind Map</strong>
                      <span style={{ fontSize: "0.72rem", color: "#64748b" }}>Connect concepts</span>
                    </button>

                    <button
                      type="button"
                      className="viora-studio-tool-card viora-tool-flashcards"
                      disabled={!canGenerateResource || busy || Boolean(generatingTool)}
                      onClick={() => void handleGenerateResource("flashcards")}
                    >
                      <strong style={{ fontSize: "0.83rem", color: "#0f172a" }}>Flashcards</strong>
                      <span style={{ fontSize: "0.72rem", color: "#64748b" }}>Active recall</span>
                    </button>

                    <button
                      type="button"
                      className="viora-studio-tool-card viora-tool-quiz"
                      disabled={!canGenerateResource || busy || Boolean(generatingTool)}
                      onClick={() => void handleGenerateResource("practice_quiz")}
                    >
                      <strong style={{ fontSize: "0.83rem", color: "#0f172a" }}>Practice Quiz</strong>
                      <span style={{ fontSize: "0.72rem", color: "#64748b" }}>Check knowledge</span>
                    </button>

                    <button
                      type="button"
                      className="viora-studio-tool-card viora-tool-exam"
                      disabled={!canGenerateResource || busy || Boolean(generatingTool)}
                      onClick={() => void handleGenerateResource("practice_exam")}
                    >
                      <strong style={{ fontSize: "0.83rem", color: "#0f172a" }}>Practice Exam</strong>
                      <span style={{ fontSize: "0.72rem", color: "#64748b" }}>Timed assessment</span>
                    </button>
                  </div>

                  <div style={{ display: "grid", gap: "0.45rem" }}>
                    <span
                      style={{
                        fontSize: "0.69rem",
                        fontWeight: 700,
                        letterSpacing: "0.06em",
                        textTransform: "uppercase",
                        color: "#64748b",
                      }}
                    >
                      Saved Resources
                    </span>

                    {resources.length === 0 ? (
                      <p className="viora-field-hint">
                        Generate notes, mind maps, flashcards, quizzes, or exams from the tools above.
                      </p>
                    ) : (
                      resources.map((res) => (
                        <button
                          key={res.resource_id}
                          type="button"
                          className="viora-saved-res-card"
                          onClick={() => setFocusedResourceId(res.resource_id)}
                        >
                          <div style={{ minWidth: 0 }}>
                            <div
                              style={{
                                fontSize: "0.83rem",
                                fontWeight: 600,
                                color: "#0f172a",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                            >
                              {res.title}
                            </div>
                            <div style={{ fontSize: "0.73rem", color: "#64748b" }}>
                              {resourceMetaSubtitle(res)}
                            </div>
                          </div>
                          <span style={{ color: "#64748b", fontSize: "0.9rem" }}>›</span>
                        </button>
                      ))
                    )}
                  </div>
                </div>
              </aside>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
