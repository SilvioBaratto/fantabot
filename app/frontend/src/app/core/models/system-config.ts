/**
 * `GET /system/config` — what `fantabot config-check` prints.
 *
 * `settings` is deliberately untyped beyond `unknown`: it is `Settings.model_dump()`
 * minus the secret fields, so a field added in Python must reach this page without a
 * matching edit here. Typing it would make the page silently drop new settings, which is
 * the opposite of what a "resolved settings" screen is for.
 */
export interface SystemConfig {
  settings: Record<string, unknown>;
  /** Field name -> whether it holds anything. Never the value, never its length. */
  secrets_set: Record<string, boolean>;
  /** Masked and paste-safe. Empty when `database_url_error` is set. */
  database_url: string;
  /** Why the DSN could not be rendered, or null. The rest of the report still arrives. */
  database_url_error: string | null;
}
