/** Validate the browser origin before the development proxy rewrites it. */
export function devOriginAllowed(origin: string | undefined, host: string | undefined): boolean {
  return origin === undefined || (host !== undefined && origin === `http://${host}`);
}
