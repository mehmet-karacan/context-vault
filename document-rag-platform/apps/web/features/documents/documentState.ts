import {
  isAccessFailure,
  isTransientFailure,
  problemMessage,
} from "../../lib/api/client";
import type { Schemas } from "../../lib/api/generated";
export type DocumentItem = Schemas["DocumentResponse"];
export type DocumentState = {
  scope: string;
  request: number;
  documents: DocumentItem[] | null;
  error: string | null;
  updating: boolean;
  denied: boolean;
};
export function emptyDocumentState(scope: string): DocumentState {
  return {
    scope,
    request: 0,
    documents: scope ? null : [],
    error: null,
    updating: false,
    denied: false,
  };
}
type Action = { scope: string; request: number } & (
  | { type: "start" }
  | { type: "loaded"; documents: DocumentItem[] }
  | { type: "failed"; cause: unknown }
  | { type: "removed"; id: string }
);
export function documentReducer(
  state: DocumentState,
  action: Action,
): DocumentState {
  if (action.type === "start") {
    if (action.request < state.request) return state;
    return {
      ...(state.scope === action.scope
        ? state
        : emptyDocumentState(action.scope)),
      request: action.request,
      updating: true,
    };
  }
  if (state.scope !== action.scope || state.request !== action.request)
    return state;
  if (action.type === "loaded")
    return {
      ...state,
      documents: action.documents,
      error: null,
      updating: false,
      denied: false,
    };
  if (action.type === "removed")
    return {
      ...state,
      documents:
        state.documents?.filter((item) => item.id !== action.id) ?? null,
    };
  return {
    ...state,
    documents: isTransientFailure(action.cause) ? state.documents : null,
    error: problemMessage(action.cause),
    updating: false,
    denied: state.denied || isAccessFailure(action.cause),
  };
}
