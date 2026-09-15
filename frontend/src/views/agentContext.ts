import type { ContextSelectionCreate } from '../api/backend'
import type { SearchHitRead, SearchTargetKind } from '../api/types'
import { SEARCH_TARGET_KINDS } from '../contracts/enums'

/** The canonical selection body type without the route's nullable wrapper. */
type Selection = NonNullable<ContextSelectionCreate>

/**
 * A search hit the human explicitly chose to hand to the Agent (Phase 12).
 *
 * This is NOT a second context model: it is a bounded presentation record whose
 * `target_id` is projected into the ONE canonical `ContextSelectionCreate` at
 * the moment the turn is sent. `title` exists only so the user can see what was
 * selected before sending.
 */
export type AgentContextItem = {
  target_kind: SearchTargetKind
  target_id: string
  title: string
}

/**
 * Which canonical `ContextSelectionCreate` id list addresses a search target
 * kind. `null` means the kind is deliberately NOT Agent-context-selectable
 * (private conversation hits are working memory, never shared context).
 */
export function contextSelectionField(
  kind: SearchTargetKind,
): 'series_ids' | 'note_ids' | 'evidence_ids' | 'decision_ids' | 'reference_ids' | null {
  switch (kind) {
    case 'scientific_object_series':
      return 'series_ids'
    case 'note':
      return 'note_ids'
    case 'evidence':
      return 'evidence_ids'
    case 'decision':
      return 'decision_ids'
    case 'run_reference':
    case 'artifact_reference':
    case 'literature_reference':
    case 'external_reference':
      return 'reference_ids'
    default:
      return null
  }
}

export function isContextSelectable(kind: SearchTargetKind): boolean {
  return contextSelectionField(kind) !== null
}

export function contextItemFromHit(hit: SearchHitRead): AgentContextItem | null {
  if (!isContextSelectable(hit.target_kind)) return null
  return { target_kind: hit.target_kind, target_id: hit.target_id, title: hit.title }
}

/**
 * Project explicit hand-off items into typed selection id lists. Order is
 * preserved and duplicates are removed, so the request that reaches the backend
 * is stable and never contains an opaque mixed `resource_ids` bag.
 */
export function contextItemsToSelection(items: AgentContextItem[]): Partial<Selection> {
  const selection: Partial<Selection> = {}
  for (const item of items) {
    const field = contextSelectionField(item.target_kind)
    if (!field) continue
    const current = (selection[field] as string[] | null | undefined) ?? []
    if (!current.includes(item.target_id)) current.push(item.target_id)
    selection[field] = current
  }
  return selection
}

/** Human-readable label for one backend-owned search target kind. */
export function targetKindLabel(kind: SearchTargetKind): string {
  switch (kind) {
    case 'scientific_object_series':
      return 'Scientific objects'
    case 'evidence':
      return 'Evidence'
    case 'decision':
      return 'Decisions'
    case 'note':
      return 'Notes'
    case 'run_reference':
      return 'Runs'
    case 'artifact_reference':
      return 'Artifacts'
    case 'literature_reference':
      return 'Literature'
    case 'external_reference':
      return 'External references'
    case 'conversation':
      return 'My conversations (private working memory)'
    default:
      return kind
  }
}

/** The closed, generated target-kind list in canonical order. */
export const SEARCH_TARGET_KIND_ORDER: readonly SearchTargetKind[] = SEARCH_TARGET_KINDS
