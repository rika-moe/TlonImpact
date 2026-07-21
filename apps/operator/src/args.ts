/** Command-line / environment configuration for the Operator TUI. */

export interface CliOptions {
  host: string;
  wsPort: number;
  restPort: number;
}

const HELP = `Tlon Operator - operations console TUI

Usage: operator [--host HOST] [--port PORT] [--rest-port PORT]

Options:
  --host        Server host        (default: 127.0.0.1, env TLON_HOST)
  --port        WebSocket port     (default: 8888,      env TLON_PORT)
  --rest-port   REST API port      (default: 8080,      env TLON_REST_PORT)
  --help        Show this help
`;

/** Parse CLI args with environment variable fallback. Exits on --help/invalid input. */
export function parseArgs(argv: string[], env: NodeJS.ProcessEnv = process.env): CliOptions {
  const options: CliOptions = {
    host: env["TLON_HOST"] ?? "127.0.0.1",
    wsPort: parsePort(env["TLON_PORT"], 8888),
    restPort: parsePort(env["TLON_REST_PORT"], 8080),
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    switch (arg) {
      case "--host":
        options.host = requireValue(argv, (i += 1), arg);
        break;
      case "--port":
      case "-p":
        options.wsPort = parsePort(requireValue(argv, (i += 1), arg), options.wsPort);
        break;
      case "--rest-port":
      case "-r":
        options.restPort = parsePort(requireValue(argv, (i += 1), arg), options.restPort);
        break;
      case "--help":
        process.stdout.write(HELP);
        process.exit(0);
      // fallthrough unreachable
      default:
        if (arg?.startsWith("--host=")) {
          options.host = arg.slice("--host=".length);
        } else if (arg?.startsWith("--port=")) {
          options.wsPort = parsePort(arg.slice("--port=".length), options.wsPort);
        } else if (arg?.startsWith("--rest-port=")) {
          options.restPort = parsePort(arg.slice("--rest-port=".length), options.restPort);
        } else {
          process.stderr.write(`Unknown argument: ${String(arg)}\n\n${HELP}`);
          process.exit(2);
        }
    }
  }

  return options;
}

function requireValue(argv: string[], index: number, flag: string): string {
  const value = argv[index];
  if (value === undefined) {
    process.stderr.write(`Missing value for ${flag}\n`);
    process.exit(2);
  }
  return value;
}

function parsePort(value: string | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  const port = Number.parseInt(value, 10);
  if (Number.isNaN(port) || port <= 0 || port > 65535) {
    process.stderr.write(`Invalid port: ${value}\n`);
    process.exit(2);
  }
  return port;
}
