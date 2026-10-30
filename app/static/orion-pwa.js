/* Orion PWA: opt-in platform-admin notification test; never ask silently. */
(() => {
  "use strict";
  const API = "/api/pwa/admin/";
  let mountedHost = null;

  function fromBase64URL(value) {
    const clean = value.replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(clean + "=".repeat((4 - clean.length % 4) % 4));
    return Uint8Array.from(raw, ch => ch.charCodeAt(0));
  }
  function installed() {
    return navigator.standalone === true ||
      (window.matchMedia && matchMedia("(display-mode: standalone)").matches);
  }
  function supported() {
    return window.isSecureContext && "serviceWorker" in navigator &&
      "PushManager" in window && "Notification" in window;
  }
  function ios() {
    return /iPhone|iPad|iPod/i.test(navigator.userAgent);
  }

  async function mount(host, request) {
    if (!host || typeof request !== "function") return;
    mountedHost = host;
    host.innerHTML = '<p class="os-help" role="status">Consultando notificações do aplicativo…</p>';

    let config;
    try { config = await request(API + "config"); }
    catch (_) {
      if (host.isConnected) host.textContent = "Não foi possível consultar as notificações. Atualize o Orion.";
      return;
    }
    if (!host.isConnected || mountedHost !== host) return;
    if (config.enabled !== true) {
      host.textContent = "O envio de testes ainda não está habilitado neste ambiente.";
      return;
    }
    host.innerHTML = [
      '<p class="os-help">Receba avisos do Orion na tela de bloqueio, mesmo com o painel fechado. ',
      'Para este teste, apenas a conta de administração pode ativar o dispositivo.</p>',
      '<p id="orionPushHint" class="os-help"></p>',
      '<div class="os-form-actions" style="display:flex;flex-wrap:wrap;gap:8px">',
      '<button class="os-button" id="orionPushActivate" type="button">Ativar e enviar teste</button>',
      '<button class="os-button os-outline" id="orionPushTest" type="button" hidden>Enviar novo teste</button>',
      '<button class="os-button os-outline" id="orionPushDisable" type="button" hidden>Desativar neste dispositivo</button>',
      '</div>',
      '<p id="orionPushFeedback" class="os-help" role="status" aria-live="polite"></p>'
    ].join("");
    const byId = id => host.querySelector("#" + id);
    const feedback = text => { if (host.isConnected) byId("orionPushFeedback").textContent = text; };
    const enabled = value => {
      host.querySelectorAll("button").forEach(b => b.disabled = !value);
    };
    const activate = byId("orionPushActivate");
    const test = byId("orionPushTest");
    const disable = byId("orionPushDisable");
    let registration;

    if (!supported()) {
      activate.hidden = true;
      byId("orionPushHint").textContent =
        "Este navegador não oferece notificações push. Abra o Orion pelo aplicativo na tela inicial do iPhone.";
      return;
    }
    if (ios() && !installed()) {
      activate.hidden = true;
      byId("orionPushHint").textContent =
        "No Safari, toque em Compartilhar → Adicionar à Tela de Início e ative Abrir como App. Depois abra o Orion pelo novo ícone e faça login.";
      return;
    }
    if (Notification.permission === "denied") {
      activate.hidden = true;
      byId("orionPushHint").textContent =
        "Notificações bloqueadas neste dispositivo. Libere o Orion nos Ajustes → Notificações do iPhone.";
      return;
    }
    try {
      registration = await navigator.serviceWorker.register("/pwa-sw.js", {scope: "/panel"});
      const existing = await registration.pushManager.getSubscription();
      if (!host.isConnected || mountedHost !== host) return;
      if (existing) {
        activate.textContent = "Reativar neste dispositivo";
        test.hidden = false;
        disable.hidden = false;
        feedback("Este aparelho já possui uma inscrição de notificações.");
      }
    } catch (_) {
      activate.disabled = true;
      feedback("Não foi possível preparar o aplicativo para notificações.");
      return;
    }

    activate.addEventListener("click", async () => {
      // Permission is requested only from an explicit tap.
      const choice = Notification.permission === "granted"
        ? Promise.resolve("granted") : Notification.requestPermission();
      enabled(false);
      feedback("Ativando notificações neste aparelho…");
      try {
        if ((await choice) !== "granted") {
          feedback("Permissão não concedida. Você pode alterar isso nos Ajustes do dispositivo.");
          return;
        }
        let sub = await registration.pushManager.getSubscription();
        if (!sub) sub = await registration.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: fromBase64URL(config.vapid_public_key)
        });
        const {endpoint, keys} = sub.toJSON();
        await request(API + "subscribe", "POST", {endpoint, keys});
        activate.textContent = "Reativar neste dispositivo";
        test.hidden = false;
        disable.hidden = false;
        feedback("Notificações ativadas. Enviando teste ao seu iPhone…");
        try {
          const result = await request(API + "test", "POST", {endpoint});
          feedback(result.accepted_by_push_service
            ? "Teste aceito pelo serviço push. Confira as notificações do seu iPhone."
            : "Notificações ativadas, mas o teste ainda não foi confirmado.");
        } catch (error) {
          feedback("Notificações ativadas. O teste falhou: " +
            (error.message || "Tente novamente pelo botão Enviar novo teste."));
        }
      } catch (error) {
        feedback(error.message || "Não foi possível ativar as notificações.");
      } finally { enabled(true); }
    });

    test.addEventListener("click", async () => {
      enabled(false);
      feedback("Solicitando notificação de teste…");
      try {
        const sub = await registration.pushManager.getSubscription();
        if (!sub) throw Error("Ative novamente as notificações neste dispositivo.");
        const result = await request(API + "test", "POST", {endpoint: sub.endpoint});
        feedback(result.accepted_by_push_service
          ? "O serviço push aceitou a notificação. Confira a tela de bloqueio."
          : "O envio ainda não foi confirmado.");
      } catch (error) {
        feedback(error.message || "Não foi possível enviar a notificação.");
      } finally { enabled(true); }
    });

    disable.addEventListener("click", async () => {
      enabled(false);
      try {
        const sub = await registration.pushManager.getSubscription();
        if (sub) {
          await request(API + "unsubscribe", "POST", {endpoint: sub.endpoint});
          await sub.unsubscribe();
        }
        activate.textContent = "Ativar e enviar teste";
        test.hidden = true; disable.hidden = true;
        feedback("Notificações desativadas neste dispositivo.");
      } catch (error) {
        feedback(error.message || "Não foi possível desativar as notificações.");
      } finally { enabled(true); }
    });
  }

  window.OrionPWA = Object.freeze({mount});
})();
