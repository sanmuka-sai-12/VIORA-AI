import { useState, type FormEvent, type ReactNode } from "react";
import {
  ActiveBadge,
  ConfirmDialog,
  DataTable,
  ModalDialog,
  PaginationBar,
  SectionCard,
  type TableColumn,
} from "../../components/common/UiPrimitives";
import { ErrorState, LoadingSpinner } from "../../components/common/FeedbackStates";
import type { useCursorList } from "./useCursorList";

export type ActiveFilterValue = "all" | "active" | "archived";

export interface CrudEntityBase {
  id: string;
  name: string;
  active: boolean;
}

export interface AcademicCrudSectionProps<TRow extends CrudEntityBase> {
  entityLabel: string;
  list: ReturnType<typeof useCursorList<TRow>>;
  activeFilter: ActiveFilterValue;
  onActiveFilterChange: (next: ActiveFilterValue) => void;
  statusFilterAriaLabel?: string;
  canCreate: boolean;
  createTitle?: string;
  createSubmitLabel: string;
  createFormClassName?: string;
  inlineCreateInDirectory?: boolean;
  renderCreateFields: (busy: boolean) => ReactNode;
  onCreate: () => Promise<boolean>;
  directoryTitle: string;
  extraFilters?: ReactNode;
  showStatusFilter?: boolean;
  loadingMessage: string;
  errorTitle: string;
  emptyTitle: string;
  emptyDescription: string;
  columns: TableColumn<TRow>[];
  canEditRow?: (row: TRow) => boolean;
  canToggleRow?: (row: TRow) => boolean;
  editButtonLabel?: string;
  extraRowActions?: (row: TRow) => ReactNode;
  editModalTitle: string;
  onOpenEdit: (row: TRow) => void;
  renderEditFields: (row: TRow, busy: boolean) => ReactNode;
  onSaveEdit: (row: TRow) => Promise<boolean>;
  archiveMessage: (row: TRow) => string;
  activateMessage?: (row: TRow) => string;
  onToggleLifecycle: (row: TRow) => Promise<void>;
}

export function AcademicCrudSection<TRow extends CrudEntityBase>({
  entityLabel,
  list,
  activeFilter,
  onActiveFilterChange,
  statusFilterAriaLabel,
  canCreate,
  createTitle,
  createSubmitLabel,
  createFormClassName = "viora-row",
  inlineCreateInDirectory = false,
  renderCreateFields,
  onCreate,
  directoryTitle,
  extraFilters,
  showStatusFilter = true,
  loadingMessage,
  errorTitle,
  emptyTitle,
  emptyDescription,
  columns,
  canEditRow = () => false,
  canToggleRow = () => false,
  editButtonLabel = "Edit",
  extraRowActions,
  editModalTitle,
  onOpenEdit,
  renderEditFields,
  onSaveEdit,
  archiveMessage,
  activateMessage,
  onToggleLifecycle,
}: AcademicCrudSectionProps<TRow>) {
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<TRow | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [lifecycleTarget, setLifecycleTarget] = useState<TRow | null>(null);
  const [toggling, setToggling] = useState(false);

  const handleCreateSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      await onCreate();
    } finally {
      setCreating(false);
    }
  };

  const handleEditSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setSavingEdit(true);
    try {
      const ok = await onSaveEdit(editing);
      if (ok) setEditing(null);
    } finally {
      setSavingEdit(false);
    }
  };

  const handleConfirmToggle = async () => {
    if (!lifecycleTarget) return;
    setToggling(true);
    try {
      await onToggleLifecycle(lifecycleTarget);
    } finally {
      setLifecycleTarget(null);
      setToggling(false);
    }
  };

  const createForm = (
    <form
      onSubmit={(e) => void handleCreateSubmit(e)}
      className={createFormClassName}
      style={{ alignItems: "flex-end" }}
    >
      {renderCreateFields(creating)}
      <div>
        <button type="submit" className="viora-btn viora-btn-primary" disabled={creating}>
          {creating ? "Creating..." : createSubmitLabel}
        </button>
      </div>
    </form>
  );

  const hasAnyRowActions =
    Boolean(extraRowActions) ||
    list.items.some((r) => canEditRow(r) || canToggleRow(r));

  const tableColumns: TableColumn<TRow>[] = [
    ...columns,
    { key: "status", header: "Status", render: (row) => <ActiveBadge active={row.active} /> },
    ...(hasAnyRowActions
      ? [
          {
            key: "actions",
            header: "Actions",
            render: (row: TRow) => (
              <div className="viora-row">
                {extraRowActions?.(row)}
                {canEditRow(row) && (
                  <button
                    type="button"
                    className="viora-btn viora-btn-sm"
                    onClick={() => {
                      onOpenEdit(row);
                      setEditing(row);
                    }}
                  >
                    {editButtonLabel}
                  </button>
                )}
                {canToggleRow(row) && (
                  <button
                    type="button"
                    className={`viora-btn viora-btn-sm ${row.active ? "viora-btn-danger" : ""}`}
                    onClick={() => setLifecycleTarget(row)}
                  >
                    {row.active ? "Archive" : "Activate"}
                  </button>
                )}
              </div>
            ),
          },
        ]
      : []),
  ];

  const directoryBody = (
    <>
      {list.loading && <LoadingSpinner message={loadingMessage} />}
      {!list.loading && list.error && (
        <ErrorState
          title={errorTitle}
          message={`${list.error.message} (${list.error.code})`}
          onRetry={list.reload}
        />
      )}
      {!list.loading && !list.error && (
        <>
          <DataTable
            rows={list.items}
            rowKey={(r) => r.id}
            emptyTitle={emptyTitle}
            emptyDescription={emptyDescription}
            columns={tableColumns}
          />
          <PaginationBar
            limit={list.limit}
            onLimitChange={list.setLimit}
            hasPrevious={list.hasPrevious}
            hasNext={list.hasNext}
            onPrevious={list.goPrevious}
            onNext={list.goNext}
            count={list.items.length}
          />
        </>
      )}
    </>
  );

  return (
    <>
      {canCreate && !inlineCreateInDirectory && createTitle && (
        <SectionCard title={createTitle}>{createForm}</SectionCard>
      )}

      <SectionCard
        title={directoryTitle}
        actions={
          <div className="viora-row">
            {extraFilters}
            {showStatusFilter && (
              <select
                className="viora-select viora-select-inline"
                aria-label={statusFilterAriaLabel ?? `Filter ${entityLabel.toLowerCase()}s by status`}
                value={activeFilter}
                onChange={(e) => {
                  onActiveFilterChange(e.target.value as ActiveFilterValue);
                  list.resetToFirstPage();
                }}
              >
                <option value="all">All statuses</option>
                <option value="active">Active only</option>
                <option value="archived">Archived only</option>
              </select>
            )}
          </div>
        }
      >
        {inlineCreateInDirectory && canCreate ? (
          <div className="viora-stack">
            {createForm}
            {directoryBody}
          </div>
        ) : (
          directoryBody
        )}
      </SectionCard>

      <ModalDialog open={editing !== null} title={editModalTitle} onClose={() => setEditing(null)}>
        {editing && (
          <form onSubmit={(e) => void handleEditSubmit(e)} className="viora-stack">
            {renderEditFields(editing, savingEdit)}
            <div className="viora-row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="viora-btn" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button type="submit" className="viora-btn viora-btn-primary" disabled={savingEdit}>
                {savingEdit ? "Saving..." : "Save changes"}
              </button>
            </div>
          </form>
        )}
      </ModalDialog>

      <ConfirmDialog
        open={lifecycleTarget !== null}
        title={
          lifecycleTarget?.active
            ? `Archive ${entityLabel.toLowerCase()}?`
            : `Activate ${entityLabel.toLowerCase()}?`
        }
        message={
          lifecycleTarget?.active
            ? archiveMessage(lifecycleTarget)
            : (activateMessage?.(lifecycleTarget!) ??
              `Reactivating "${lifecycleTarget?.name ?? ""}" restores active status.`)
        }
        confirmLabel={lifecycleTarget?.active ? "Archive" : "Activate"}
        danger={Boolean(lifecycleTarget?.active)}
        busy={toggling}
        onCancel={() => setLifecycleTarget(null)}
        onConfirm={() => void handleConfirmToggle()}
      />
    </>
  );
}
