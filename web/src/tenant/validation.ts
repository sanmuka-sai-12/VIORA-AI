const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isValidUuid(value: string): boolean {
  return UUID_REGEX.test(value.trim());
}

export function validateRequiredName(
  value: string,
  fieldLabel = "Name",
  maxLength = 200,
): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return `${fieldLabel} is required.`;
  }
  if (trimmed.length > maxLength) {
    return `${fieldLabel} must be ${maxLength} characters or fewer.`;
  }
  return null;
}

export function validateRequiredUuid(
  value: string,
  fieldLabel = "User ID",
): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return `${fieldLabel} is required.`;
  }
  if (!isValidUuid(trimmed)) {
    return `${fieldLabel} must be a valid UUID.`;
  }
  return null;
}

export function validatePasswordChange(
  currentPassword: string,
  newPassword: string,
  confirmPassword: string,
): string | null {
  if (!currentPassword) {
    return "Current password is required.";
  }
  if (newPassword.length < 12 || newPassword.length > 1024) {
    return "New password must be between 12 and 1024 characters.";
  }
  if (newPassword !== confirmPassword) {
    return "New password confirmation does not match.";
  }
  if (newPassword === currentPassword) {
    return "New password must differ from current password.";
  }
  return null;
}

export interface CursorPageState {
  currentCursor: string | null;
  history: (string | null)[];
  nextCursor: string | null;
}

export function createInitialPageState(): CursorPageState {
  return {
    currentCursor: null,
    history: [],
    nextCursor: null,
  };
}

export function advanceCursorPage(
  state: CursorPageState,
  nextCursor: string,
): CursorPageState {
  return {
    currentCursor: nextCursor,
    history: [...state.history, state.currentCursor],
    nextCursor: null,
  };
}

export function rewindCursorPage(state: CursorPageState): CursorPageState {
  if (state.history.length === 0) {
    return state;
  }
  const nextHistory = [...state.history];
  const previousCursor = nextHistory.pop() ?? null;
  return {
    currentCursor: previousCursor,
    history: nextHistory,
    nextCursor: null,
  };
}
