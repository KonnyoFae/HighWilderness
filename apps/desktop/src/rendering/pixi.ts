// Pixi's CSP-compatible implementations avoid dynamic Function/eval generation.
// Load them for every entry (including fixtures) before constructing a renderer.
import "pixi.js/unsafe-eval";
export { Application, Container, Graphics } from "pixi.js";
