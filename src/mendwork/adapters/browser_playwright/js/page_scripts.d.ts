// Type-only declarations shared by the page scripts in this directory.

/** Mendwork's per-document state, installed by page_state.js as window.__mendwork. */
interface MendworkPageState {
  /** Identifies the document; a new document gets a new state and a new token. */
  readonly token: string;
  /** How many DOM mutation batches this document has seen since the state was installed. */
  mutations(): number;
  /** Resolves true when the mutation count moves past `since`, or false at the timeout. */
  whenChanged(since: number, timeoutMs: number): Promise<boolean>;
  /** Resolves true after `frames` animation frames without a mutation, or false at the timeout. */
  whenQuiet(frames: number, timeoutMs: number): Promise<boolean>;
}

type PageStateRequest =
  | { operation: "epoch"; token: string }
  | { operation: "settle"; token: string; frames: number; timeoutMs: number }
  | { operation: "change"; token: string; document: string; since: number; timeoutMs: number };

interface PageStateReply {
  document: string;
  mutations: number;
  quiet: boolean;
  changed: boolean;
}

interface MendworkIdentity {
  tag: string;
  type: string | null;
  role: string | null;
  name: string;
  /** A form control inside the name, whose value would be part of the name; never read. */
  embeddedControl: boolean;
  connected: boolean;
}

/** Where a name is being computed from, which changes what contributes to it. */
interface IdentityTraversal {
  labelledBy: boolean;
  label: boolean;
  descendant: boolean;
}

interface FieldValueRequest {
  element: Element;
  /** The value the field should hold, or null to require only that it is not empty. */
  expected: string | null;
  /** How long to wait for a match before answering with the field's state as it is. */
  timeoutMs: number;
}

interface FieldValueState {
  matches: boolean;
  empty: boolean;
}

interface Window {
  __mendwork?: MendworkPageState;
}
