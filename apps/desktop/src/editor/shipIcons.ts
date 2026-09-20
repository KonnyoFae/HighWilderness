export const SHIP_ICONS = { circle: ['●', '圆点'], diamond: ['◆', '菱形'], triangle: ['▲', '三角'], square: ['■', '方形'], cross: ['✚', '十字'], star: ['★', '星形'] } as const;
export const shipIcon = (id?: string) => SHIP_ICONS[id as keyof typeof SHIP_ICONS] ?? SHIP_ICONS.circle;
