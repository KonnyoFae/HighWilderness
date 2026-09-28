// Reuses the durable real-service fixture and adds AV2 flight acceptance.
process.env.HW_AVIATION_FLIGHT='1';
process.env.HW_AVIATION_OUT??=`artifacts/aviation-av2-${Date.now()}`;
await import('./verify_aviation_view.mjs');
