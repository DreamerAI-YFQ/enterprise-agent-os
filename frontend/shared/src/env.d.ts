/**
 * Minimal Vite env type declarations for the shared package.
 * Avoids a hard dependency on `vite/client` types while still typing
 * `import.meta.env.VITE_*` access for consumers running under Vite.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly [key: `VITE_${string}`]: string | undefined;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
