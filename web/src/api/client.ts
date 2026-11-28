import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./generated";

export type ApiSchema<T extends keyof components["schemas"]> =
  components["schemas"][T];

export type ErrorResponse = ApiSchema<"ErrorResponse">;
export type TokenResponse = ApiSchema<"TokenResponse">;
export type AuthenticatedUser = ApiSchema<"AuthenticatedUser">;
export type AccountStatus = ApiSchema<"AccountStatus">;
export type InstitutionDiscovery = ApiSchema<"InstitutionDiscovery">;
export type InstitutionInfo = ApiSchema<"InstitutionInfo">;
export type InstitutionRole = ApiSchema<"InstitutionRole">;
export type MembershipStatus = ApiSchema<"MembershipStatus">;
export type MemberSummary = ApiSchema<"MemberSummary">;
export type MemberOnboardCreate = ApiSchema<"MemberOnboardCreate">;
export type MemberOnboardResponse = ApiSchema<"MemberOnboardResponse">;
export type StudentProfileSummary = ApiSchema<"StudentProfileSummary">;
export type StudentProfileUpdate = ApiSchema<"StudentProfileUpdate">;
export type FacultyProfileSummary = ApiSchema<"FacultyProfileSummary">;
export type FacultyProfileUpdate = ApiSchema<"FacultyProfileUpdate">;
export type AssignedSectionSummary = ApiSchema<"AssignedSectionSummary">;
export type AssignedOfferingSummary = ApiSchema<"AssignedOfferingSummary">;
export type FacultyResponsibilitiesSummary = ApiSchema<"FacultyResponsibilitiesSummary">;
export type MyClassroomsSummary = ApiSchema<"MyClassroomsSummary">;
export type ClassroomAnnouncementSummary = ApiSchema<"ClassroomAnnouncementSummary">;
export type CampusSummary = ApiSchema<"CampusSummary">;
export type DepartmentSummary = ApiSchema<"DepartmentSummary">;
export type DepartmentHeadSummary = ApiSchema<"DepartmentHeadSummary">;
export type ProgrammeSummary = ApiSchema<"ProgrammeSummary">;
export type AcademicYearSummary = ApiSchema<"AcademicYearSummary">;
export type SemesterSummary = ApiSchema<"SemesterSummary">;
export type SectionSummary = ApiSchema<"SectionSummary">;
export type ClassCoordinatorSummary = ApiSchema<"ClassCoordinatorSummary">;
export type SubjectSummary = ApiSchema<"SubjectSummary">;
export type SubjectOfferingSummary = ApiSchema<"SubjectOfferingSummary">;
export type OfferingAssignmentSummary = ApiSchema<"OfferingAssignmentSummary">;
export type GuardianLinkSummary = ApiSchema<"GuardianLinkSummary">;
export type StudentSummary = ApiSchema<"StudentSummary">;
export type OfferingSourcePolicyInfo = ApiSchema<"OfferingSourcePolicyInfo">;
export type SourceSummary = ApiSchema<"SourceSummary">;
export type SourceRegistrationResult = ApiSchema<"SourceRegistrationResult">;
export type SourceVersionSummary = ApiSchema<"SourceVersionSummary">;
export type WorkflowJobSummary = ApiSchema<"WorkflowJobSummary">;
export type DerivedExtractionRecord = ApiSchema<"DerivedExtractionRecord">;
export type ConceptExtractionRecord = ApiSchema<"ConceptExtractionRecord">;
export type KnowledgePublicationState = ApiSchema<"KnowledgePublicationState">;
export type KnowledgeReviewRecord = ApiSchema<"KnowledgeReviewRecord">;
export type PublishedLessonPackage = ApiSchema<"PublishedLessonPackage">;
export type KnowledgeIndexStatus = ApiSchema<"KnowledgeIndexStatus">;
export type KnowledgeQARequest = ApiSchema<"KnowledgeQARequest">;
export type KnowledgeQAResult = ApiSchema<"KnowledgeQAResult">;
export type QACitation = ApiSchema<"QACitation">;
export type GroundedQAClaim = ApiSchema<"GroundedQAClaim">;
export type QAInterpretation = ApiSchema<"QAInterpretation">;
export type QAClarification = ApiSchema<"QAClarification">;
export type QAAbstention = ApiSchema<"QAAbstention">;
export type LessonBlueprint = ApiSchema<"LessonBlueprint">;
export type LessonBlueprintRecord = ApiSchema<"LessonBlueprintRecord">;
export type LessonBlueprintSummary = ApiSchema<"LessonBlueprintSummary">;
export type LessonBlueprintApprovalRequest = ApiSchema<"LessonBlueprintApprovalRequest">;
export type LessonBlueprintApprovalState = ApiSchema<"LessonBlueprintApprovalState">;
export type PublishedLessonBlueprint = ApiSchema<"PublishedLessonBlueprint">;
export type BlueprintAssessmentItem = ApiSchema<"BlueprintAssessmentItem">;
export type StudentSafeAssessmentItem = ApiSchema<"StudentSafeAssessmentItem">;
export type TypedVisualSceneSpec = ApiSchema<"TypedVisualSceneSpec">;
export type PersonalLearningAttachment = ApiSchema<"PersonalLearningAttachment">;
export type PersonalLearningAttachmentKind = PersonalLearningAttachment["kind"];
export type PersonalLearningAttachmentStatus = PersonalLearningAttachment["status"];
export type PersonalLearningAttachmentRef = ApiSchema<"PersonalLearningAttachmentRef">;
export type PersonalLearningAttachmentTextRequest =
  ApiSchema<"PersonalLearningAttachmentTextRequest">;
export type PersonalLearningSourceRef = ApiSchema<"PersonalLearningSourceRef">;
export type PersonalLearningTurn = ApiSchema<"PersonalLearningTurn">;
export type PersonalLearningRequest = ApiSchema<"PersonalLearningRequest">;
export type PersonalLearningResponse = ApiSchema<"PersonalLearningResponse">;
export type PersonalLearningSessionSummary =
  ApiSchema<"PersonalLearningSessionSummary">;
export type PersonalLearningMessageRecord =
  ApiSchema<"PersonalLearningMessageRecord">;
export type PersonalLearningSessionDetail =
  ApiSchema<"PersonalLearningSessionDetail">;
export type PersonalLearningSessionCreateRequest =
  ApiSchema<"PersonalLearningSessionCreateRequest">;
export type PersonalLearningSessionUpdateRequest =
  ApiSchema<"PersonalLearningSessionUpdateRequest">;
export type PersonalLearningDeleteResult =
  ApiSchema<"PersonalLearningDeleteResult">;
export type PersonalLearningStudyResource =
  ApiSchema<"PersonalLearningStudyResource">;
export type MindMapGraph = ApiSchema<"MindMapGraph">;
export type MindMapNode = ApiSchema<"MindMapNode">;
export type FlashcardDeck = ApiSchema<"FlashcardDeck">;
export type FlashcardItem = ApiSchema<"FlashcardItem">;
export type FlashcardStatus = FlashcardItem["status"];
export type PracticeQuizData = ApiSchema<"PracticeQuizData">;
export type PracticeQuizAttemptRecord = ApiSchema<"PracticeQuizAttemptRecord">;
export type PracticeExamData = ApiSchema<"PracticeExamData">;
export type PracticeExamActiveAttempt = ApiSchema<"PracticeExamActiveAttempt">;
export type PracticeExamAttemptRecord = ApiSchema<"PracticeExamAttemptRecord">;
export type QuizDifficulty = PracticeQuizData["difficulty"];
export type PersonalLearningResourceGenerateRequest =
  ApiSchema<"PersonalLearningResourceGenerateRequest">;
export type LearningToolKind = PersonalLearningStudyResource["tool_kind"];
export type LearningPromptMode = PersonalLearningRequest["mode"];

export interface NormalizedApiError {
  status: number;
  code: string;
  message: string;
  requestId?: string;
}

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function isAuthEndpoint(urlOrPath: string): boolean {
  try {
    const pathname = urlOrPath.startsWith("http")
      ? new URL(urlOrPath).pathname
      : urlOrPath;
    return pathname.startsWith("/api/v1/auth/");
  } catch {
    return urlOrPath.includes("/api/v1/auth/");
  }
}

export function isRefreshOrLoginEndpoint(urlOrPath: string): boolean {
  try {
    const pathname = urlOrPath.startsWith("http")
      ? new URL(urlOrPath).pathname
      : urlOrPath;
    return (
      pathname === "/api/v1/auth/login" || pathname === "/api/v1/auth/refresh"
    );
  } catch {
    return (
      urlOrPath.endsWith("/api/v1/auth/login") ||
      urlOrPath.endsWith("/api/v1/auth/refresh")
    );
  }
}

export function normalizeApiError(
  error: unknown,
  status: number,
  fallbackMessage = "Request failed",
): NormalizedApiError {
  if (error && typeof error === "object") {
    const record = error as Partial<ErrorResponse>;
    if (typeof record.code === "string" && typeof record.message === "string") {
      return {
        status,
        code: record.code,
        message: record.message,
        requestId:
          typeof record.request_id === "string" ? record.request_id : undefined,
      };
    }
  }
  return {
    status,
    code: status >= 500 ? "server_error" : "request_failed",
    message: fallbackMessage,
  };
}

export interface ApiClientOptions {
  baseUrl?: string;
  getAuthToken?: () => string | null;
  fetch?: typeof globalThis.fetch;
  onResponseError?: (response: Response, schemaPath: string) => void;
}

export function createApiClient(options: ApiClientOptions = {}) {
  const envApiUrl =
    typeof import.meta !== "undefined" && import.meta.env
      ? import.meta.env.VITE_API_URL
      : undefined;
  const baseUrl = options.baseUrl ?? envApiUrl ?? "";

  const authMiddleware: Middleware = {
    async onRequest({ request, schemaPath }) {
      if (!request.headers.get("X-Request-ID")) {
        const correlationId = crypto.randomUUID();
        request.headers.set("X-Request-ID", correlationId);
      }

      if (
        MUTATING_METHODS.has(request.method.toUpperCase()) &&
        isAuthEndpoint(schemaPath)
      ) {
        request.headers.set("X-Viora-CSRF", "1");
      }

      if (!isRefreshOrLoginEndpoint(schemaPath)) {
        const token = options.getAuthToken ? options.getAuthToken() : null;
        if (token) {
          request.headers.set("Authorization", `Bearer ${token}`);
        }
      }

      return request;
    },
    async onResponse({ response, schemaPath }) {
      if (!response.ok && options.onResponseError) {
        options.onResponseError(response, schemaPath);
      }
      return response;
    },
  };

  const client = createClient<paths>({
    baseUrl,
    credentials: "include",
    ...(options.fetch ? { fetch: options.fetch } : {}),
  });
  client.use(authMiddleware);
  return client;
}

export type VioraApiClient = ReturnType<typeof createApiClient>;

export const api = createApiClient();
