//! decisioner — request-path service for the realtime credit-decisioning platform.
//!
//! See `docs/decisions/004-monolithic-decisioner-microservices-where-they-help.md`
//! for the three-plane architectural reasoning and the request-path latency
//! budget. Skeleton only at Day 0; Days 2–4 build the inference + bandit +
//! champion-challenger logic.

use axum::{routing::get, Router};

#[tokio::main]
async fn main() {
    env_logger::init();

    let app = Router::new().route("/health", get(health));

    let port: u16 = std::env::var("DECISIONER_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(3000);

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port))
        .await
        .expect("failed to bind decisioner listener");

    log::info!("decisioner listening on 0.0.0.0:{}", port);
    axum::serve(listener, app)
        .await
        .expect("axum server crashed");
}

async fn health() -> &'static str {
    "ok"
}

// TODO Day 2: GET /metrics + segment router + ONNX inference module
// TODO Day 3: POST /decide handler + contextual bandit selection
// TODO Day 4: champion-challenger shadow scoring + audit log queue
