// Количества и деньги приходят строками: Decimal не должен превращаться в float.
export type Dec = string;

export interface Me {
  id: number;
  login: string;
  full_name: string;
  role: "storekeeper" | "technologist" | "manager";
  role_label: string;
  permissions: string[];
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Material {
  id: number;
  code: string;
  name: string;
  unit: string;
}

export interface MaterialStock extends Material {
  qty_on_hand: Dec;
  qty_reserved: Dec;
  qty_free: Dec;
  qty_on_check: Dec;
}

export type BatchStatus = "on_check" | "accepted" | "rejected";

export interface Batch {
  id: number;
  number: string;
  receipt_number: string;
  material: Material;
  supplier: { id: number; name: string };
  received_at: string;
  qty_initial: Dec;
  qty_on_hand: Dec;
  qty_reserved: Dec;
  qty_free: Dec;
  price: Dec;
  currency: string;
  fx_rate: Dec;
  fx_rate_date: string;
  unit_cost_rub: Dec;
  status: BatchStatus;
  status_label: string;
  comment: string | null;
}

export interface AuditEntry {
  id: number;
  at: string;
  user_full_name: string;
  entity: string;
  entity_id: number;
  action: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
}

export interface BatchCard extends Batch {
  movements: { id: number; kind_label: string; qty: Dec; job_id: number | null; created_at: string }[];
  history: AuditEntry[];
}

export interface Recipe {
  id: number;
  name: string;
  items: { material: Material; qty_per_unit: Dec }[];
}

export type JobStatus = "draft" | "planned" | "in_progress" | "completed" | "cancelled";

export interface Job {
  id: number;
  number: string;
  recipe_id: number;
  recipe_name: string;
  qty: number;
  planned_date: string;
  status: JobStatus;
  status_label: string;
  produced_qty: number | null;
  has_deficit: boolean;
}

export interface Component {
  material: Material;
  qty_required: Dec;
  qty_reserved: Dec;
  qty_deficit: Dec;
  qty_free: Dec;
  qty_issued: Dec;
  qty_actual: Dec | null;
  light: "green" | "yellow" | "red" | null;
}

export interface JobCard extends Job {
  components: Component[];
  reservations: {
    batch_id: number;
    batch_number: string;
    material_code: string;
    unit: string;
    received_at: string;
    qty: Dec;
  }[];
  history: AuditEntry[];
  marking: {
    status: "pending" | "sent" | "failed";
    status_label: string;
    attempts: number;
    next_attempt_at: string;
    log: {
      number: number;
      at: string;
      duration_ms: number;
      http_status: number | null;
      error: string | null;
    }[];
  } | null;
}

export interface JobCost {
  basis: string;
  is_complete: boolean;
  units: number;
  currency: string;
  total: Dec;
  per_unit: Dec | null;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
}

export interface ToolUse {
  name: string;
  label: string;
  ok: boolean;
  args: string;
  result: string;
}

export interface CodeRun {
  code: string;
  logs: string;
  failed: boolean;
}

export interface ChatFile {
  id: number;
  filename: string;
  mime: string;
  size: number;
  source: "upload" | "generated";
  url: string;
}

export interface Action {
  id: number;
  preview: {
    title: string;
    components: { material: string; required: string; available: string; deficit: string | null }[];
    warning: string | null;
  };
  status: "pending" | "confirmed" | "rejected" | "expired" | "failed";
  result: string | null;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  status: "streaming" | "complete" | "interrupted" | "error";
  model: string | null;
  used_fallback: boolean;
  meta: {
    note?: boolean;
    tools?: ToolUse[];
    code?: CodeRun[];
    legend?: Record<string, string>;
    error?: string | null;
  };
  files: ChatFile[];
  actions: Action[];
}

export interface Fact {
  id: number;
  fact: string;
  created_at: string;
}
