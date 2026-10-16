/**
 * Prevent legacy libsignal-node from printing live Signal session objects.
 *
 * That dependency uses console.info/warn("Closing session:", SessionEntry)
 * and related calls; Node expands the object, including ratchet private keys.
 * Install before creating a Baileys socket. The original arguments must NEVER
 * be serialized, stringified or forwarded to a logging service.
 *
 * This is intentionally narrow: ordinary logs and actual cryptographic state
 * are left unchanged. Retained Railway logs from earlier runs are NOT erased.
 */
const SESSION_OBJECT_PREFIXES = new Set([
  "Closing session:",
  "Opening session:",
  "Removing old closed session:",
  "Session already closed",
]);

export function protectSignalSessionLogs(target=console) {
  const originals = new Map();
  for(const level of ["info","warn"]) {
    const original=target?.[level];
    if(typeof original!=="function")continue;
    originals.set(level,original);
    target[level]=function(...args) {
      if(typeof args[0]==="string" &&
          SESSION_OBJECT_PREFIXES.has(args[0]) && args.length>1) {
        // Never pass args[1]: libsignal may include private ratchet keys.
        return original.call(target,"Orion QR: atualização interna de sessão Signal (dados omitidos)");
      }
      return original.apply(target,args);
    };
  }
  return function restore() {
    for(const [level,original] of originals)target[level]=original;
  };
}
