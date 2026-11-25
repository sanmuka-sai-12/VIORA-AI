import type {
  InstitutionDiscovery,
  NormalizedApiError,
  VioraApiClient,
} from "../api/client";

export type DiscoveryStatus = "idle" | "loading" | "ready" | "error";

export interface TenantSnapshot {
  status: DiscoveryStatus;
  institutions: InstitutionDiscovery[];
  selectedTenantId: string | null;
  selectedInstitution: InstitutionDiscovery | null;
  /** Incremented whenever tenant switches or clears so child caches/views reset completely. */
  epoch: number;
  error: NormalizedApiError | null;
}

export interface TenantControllerDeps {
  apiClient: VioraApiClient;
  normalizeError: (
    error: unknown,
    status: number,
    fallbackMessage?: string,
  ) => NormalizedApiError;
}

export interface TenantController {
  getSnapshot(): TenantSnapshot;
  subscribe(listener: (snapshot: TenantSnapshot) => void): () => void;
  discover(): Promise<void>;
  selectInstitution(tenantId: string): boolean;
  updateInstitutionNameLocal(tenantId: string, name: string): void;
  clear(): void;
  getCached<T>(key: string): T | undefined;
  setCached<T>(key: string, value: T): void;
  clearCache(): void;
}

export function createTenantController(
  deps: TenantControllerDeps,
): TenantController {
  const listeners = new Set<(snapshot: TenantSnapshot) => void>();
  let tenantDataCache = new Map<string, unknown>();
  let requestSequence = 0;

  let snapshot: TenantSnapshot = {
    status: "idle",
    institutions: [],
    selectedTenantId: null,
    selectedInstitution: null,
    epoch: 0,
    error: null,
  };

  function emit(next: TenantSnapshot): void {
    snapshot = next;
    for (const listener of listeners) {
      listener(snapshot);
    }
  }

  function findInstitution(
    institutions: InstitutionDiscovery[],
    tenantId: string | null,
  ): InstitutionDiscovery | null {
    if (!tenantId) {
      return null;
    }
    return institutions.find((item) => item.tenant_id === tenantId) ?? null;
  }

  async function discover(): Promise<void> {
    const seq = ++requestSequence;
    emit({
      ...snapshot,
      status: "loading",
      error: null,
    });

    try {
      const res = await deps.apiClient.GET("/api/v1/institutions");
      if (seq !== requestSequence) {
        return;
      }

      if (!res.data) {
        const err = deps.normalizeError(
          res.error,
          res.response.status,
          "Unable to load institutional memberships.",
        );
        tenantDataCache = new Map<string, unknown>();
        emit({
          status: "error",
          institutions: [],
          selectedTenantId: null,
          selectedInstitution: null,
          epoch: snapshot.epoch + 1,
          error: err,
        });
        return;
      }

      const institutions = res.data;
      const previousSelected = findInstitution(
        institutions,
        snapshot.selectedTenantId,
      );
      let nextSelected: InstitutionDiscovery | null = previousSelected;

      // Auto-select only when the user belongs to exactly one active institution.
      if (!nextSelected && institutions.length === 1) {
        nextSelected = institutions[0] ?? null;
      }

      const selectedChanged =
        (nextSelected?.tenant_id ?? null) !== snapshot.selectedTenantId;
      if (selectedChanged) {
        tenantDataCache = new Map<string, unknown>();
      }

      emit({
        status: "ready",
        institutions,
        selectedTenantId: nextSelected?.tenant_id ?? null,
        selectedInstitution: nextSelected,
        epoch: selectedChanged ? snapshot.epoch + 1 : snapshot.epoch,
        error: null,
      });
    } catch {
      if (seq !== requestSequence) {
        return;
      }
      tenantDataCache = new Map<string, unknown>();
      emit({
        status: "error",
        institutions: [],
        selectedTenantId: null,
        selectedInstitution: null,
        epoch: snapshot.epoch + 1,
        error: {
          status: 0,
          code: "network_error",
          message: "Network failure while loading your institutions.",
        },
      });
    }
  }

  function selectInstitution(tenantId: string): boolean {
    const match = findInstitution(snapshot.institutions, tenantId);
    if (!match) {
      return false;
    }
    if (snapshot.selectedTenantId === match.tenant_id) {
      return true;
    }
    // Always wipe tenant-scoped data cache before exposing the newly selected tenant.
    tenantDataCache = new Map<string, unknown>();
    emit({
      ...snapshot,
      selectedTenantId: match.tenant_id,
      selectedInstitution: match,
      epoch: snapshot.epoch + 1,
    });
    return true;
  }

  function updateInstitutionNameLocal(tenantId: string, name: string): void {
    const updated = snapshot.institutions.map((item) =>
      item.tenant_id === tenantId ? { ...item, name } : item,
    );
    const nextSelected = findInstitution(updated, snapshot.selectedTenantId);
    emit({
      ...snapshot,
      institutions: updated,
      selectedInstitution: nextSelected,
    });
  }

  function clear(): void {
    requestSequence += 1;
    tenantDataCache = new Map<string, unknown>();
    emit({
      status: "idle",
      institutions: [],
      selectedTenantId: null,
      selectedInstitution: null,
      epoch: snapshot.epoch + 1,
      error: null,
    });
  }

  return {
    getSnapshot() {
      return snapshot;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    discover,
    selectInstitution,
    updateInstitutionNameLocal,
    clear,
    getCached<T>(key: string): T | undefined {
      return tenantDataCache.get(key) as T | undefined;
    },
    setCached<T>(key: string, value: T): void {
      tenantDataCache.set(key, value);
    },
    clearCache(): void {
      tenantDataCache = new Map<string, unknown>();
    },
  };
}
