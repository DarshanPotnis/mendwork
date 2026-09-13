// Type-only declarations shared by the page scripts in this directory.

/** Mendwork's one page global: a namespace whose slots are each filled once per document. */
interface MendworkNamespace {
  pageState?: MendworkPageState;
  recorder?: MendworkRecorder;
}

/** Page state, installed by page_state.js into window.__mendwork.pageState. */
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

// Recording.

/** The recorder, installed by recorder.js into window.__mendwork.recorder. */
interface MendworkRecorder {
  /** Identifies the document the recorder is running in. */
  readonly document: string;
  /** The element a message named, if the message came from this document. */
  element(document: string, key: number): Element | null;
  /** Let the recorder's own action reach the captured element (or the page, for a key). */
  arm(document: string, key: number | null): boolean;
  /** Hold interactions back again. */
  disarm(document: string): void;
  /** Send a fill message for every field edited but not yet committed. */
  flush(): RecorderPosition;
}

interface RecorderPosition {
  document: string;
  /** The sequence number of the last message this document sent. */
  sequence: number;
}

type RecorderKey = "Enter" | "Escape" | "Space";
type RecorderIgnoredReason =
  | "modified_click"
  | "modified_key"
  | "double_click"
  | "file_input"
  | "frame"
  | "busy";

/** A message's content, before the recorder stamps its document and sequence number. */
type RecorderMessageBody =
  | { type: "click" | "fill" | "select"; element: number }
  | { type: "press"; element: number | null; key: RecorderKey }
  | { type: "ignored"; reason: RecorderIgnoredReason }
  | { type: "restored" };

/** What the page sends to Mendwork. It never carries anything a person typed. */
type RecorderMessage = RecorderMessageBody & { document: string; sequence: number };

type RecorderControlRequest =
  | { operation: "arm"; document: string; element: number | null }
  | { operation: "disarm"; document: string }
  | { operation: "flush" };

interface RecorderControlReply {
  /** The recorder's document, or null when no recorder runs in the page. */
  document: string | null;
  sequence: number;
  ok: boolean;
}

interface RecorderElementRequest {
  document: string;
  element: number;
}

interface ElementFactsRequest {
  /** How many nearby texts to report. */
  nearbyMax: number;
  /** How many levels of the structural path to keep, nearest first. */
  pathMax: number;
}

/** What element_facts.js reports. Field contents are never part of it. */
interface MendworkElementFacts {
  tag: string;
  id: string | null;
  name: string | null;
  type: string | null;
  autocomplete: string | null;
  placeholder: string | null;
  ariaLabel: string | null;
  testId: string | null;
  href: string | null;
  labelText: string | null;
  text: string | null;
  ownText: string | null;
  nearbyText: string[];
  structuralPath: string;
  box: { x: number; y: number; width: number; height: number } | null;
  textEntry: boolean;
  masked: boolean;
  inForm: boolean;
  formSubmit: boolean;
  formHasPassword: boolean;
}

interface MendworkScopeFacts {
  tag: string;
  id: string | null;
  testId: string | null;
  rowHeader: string | null;
}

// Healing.

/** Which elements a candidate scan looks for, and how many it returns at most. */
interface CandidateScanRequest {
  kind: "click" | "press" | "fill" | "select" | "none";
  limit: number;
}

/** What extract_candidates.js returns: element references and a count, nothing else. */
interface CandidateScanResult {
  elements: Element[];
  /** Every matching visible element, including those beyond the limit. */
  total: number;
}

/** A field's content, or only the fact that it is masked. */
interface MendworkFieldText {
  masked: boolean;
  value: string | null;
}

interface Window {
  __mendwork?: MendworkNamespace;
  /** Installed by Playwright for the recorder, captured and deleted before the page runs. */
  __mendwork_recorder_binding?: (message: RecorderMessage) => Promise<unknown>;
}
