/**
 * Utility functions for data transformation
 * Handles conversion between snake_case (server) and camelCase (client)
 */

/**
 * Convert snake_case string to camelCase
 */
function toCamelCase(str: string): string {
  return str.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase())
}

/**
 * Convert camelCase string to snake_case
 */
function toSnakeCase(str: string): string {
  return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`)
}

/**
 * Recursively convert object keys from snake_case to camelCase
 */
export function snakeToCamel<T = Record<string, unknown>>(obj: unknown): T {
  if (obj === null || typeof obj !== 'object') {
    return obj as T
  }

  if (Array.isArray(obj)) {
    return obj.map((item) => snakeToCamel(item)) as unknown as T
  }

  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(obj)) {
    const camelKey = toCamelCase(key)
    result[camelKey] = snakeToCamel(value)
  }
  return result as T
}

/**
 * Recursively convert object keys from camelCase to snake_case
 */
export function camelToSnake<T = Record<string, unknown>>(obj: unknown): T {
  if (obj === null || typeof obj !== 'object') {
    return obj as T
  }

  if (Array.isArray(obj)) {
    return obj.map((item) => camelToSnake(item)) as unknown as T
  }

  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(obj)) {
    const snakeKey = toSnakeCase(key)
    result[snakeKey] = camelToSnake(value)
  }
  return result as T
}

/**
 * Transform a message from server format to client format
 * Handles both field name conversion and type conversion
 */
export function transformMessage(message: Record<string, unknown>): Record<string, unknown> {
  const converted = snakeToCamel(message)
  
  // Handle timestamp conversion (ISO string to number)
  if (typeof converted.timestamp === 'string') {
    converted.timestamp = new Date(converted.timestamp).getTime()
  }
  
  return converted
}

/**
 * Transform agent data from server format to client format
 * Ensures status field is populated from state and role from config
 */
export function transformAgent(agent: Record<string, unknown>): Record<string, unknown> {
  const converted = snakeToCamel(agent)
  
  // Map state to status for compatibility
  if (converted.state && !converted.status) {
    converted.status = converted.state
  }
  
  // Extract role from config if available
  if (converted.config && typeof converted.config === 'object') {
    const config = converted.config as Record<string, unknown>
    if (config.role && !converted.role) {
      converted.role = config.role
    }
    if (config.skills && !converted.skills) {
      converted.skills = config.skills
    }
  }
  
  return converted
}
