// static/js/auth.js
(function(){
  const API_BASE = `${location.protocol}//${location.host}`;
  const api = {
    me: async () => {
      try{
        const r = await fetch(`${API_BASE}/api/auth/me`, {credentials:'include'});
        return await r.json();
      }catch(e){ return {user:null}; }
    },
    login: async (email, password) => {
      try{
        const r = await fetch(`${API_BASE}/api/auth/login`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          credentials:'include',
          body: JSON.stringify({email, password})
        });
        let body = {};
        try{ body = await r.json(); }catch(_){ body = {}; }
        return {ok:r.ok, body};
      }catch(e){
        return {ok:false, body:{error:"Server bilan bog'lanishda xatolik"}};
      }
    },
    signup: async (payload) => {
      try{
        const r = await fetch(`${API_BASE}/api/auth/signup`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          credentials:'include',
          body: JSON.stringify(payload)
        });
        let body = {};
        try{ body = await r.json(); }catch(_){ body = {}; }
        return {ok:r.ok, body};
      }catch(e){
        return {ok:false, body:{error:"Server bilan bog'lanishda xatolik"}};
      }
    },
    signupConfirm: async (email, code) => {
      try{
        const r = await fetch(`${API_BASE}/api/auth/signup/confirm`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          credentials:'include',
          body: JSON.stringify({email, code})
        });
        let body = {};
        try{ body = await r.json(); }catch(_){ body = {}; }
        return {ok:r.ok, body};
      }catch(e){
        return {ok:false, body:{error:"Server bilan bog'lanishda xatolik"}};
      }
    },
    logout: () => fetch(`${API_BASE}/api/auth/logout`, {method:'POST', credentials:'include'}).then(r=>r.json())
  };

  function injectAuthUI(){
    // if (document.getElementById('auth-bar')) return;
    // const bar = document.createElement('div');
    // bar.id = 'auth-bar';
    // bar.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:10000;display:flex;gap:10px;align-items:center;background:rgba(0,0,0,0.4);backdrop-filter:blur(10px);padding:8px 12px;border-radius:12px;color:#fff;font:500 14px Inter,system-ui;box-shadow:0 4px 18px rgba(0,0,0,.15)';
    // bar.innerHTML = `
    //   <span id="auth-user" style="opacity:.9"></span>
    //   <button id="auth-login-btn" style="padding:6px 10px;border:1px solid rgba(255,255,255,.5);background:transparent;color:#fff;border-radius:8px;cursor:pointer">Sign in</button>
    //   <button id="auth-signup-btn" style="padding:6px 10px;border:0;background:#2563eb;color:#fff;border-radius:8px;cursor:pointer">Sign up</button>
    //   <button id="auth-logout-btn" style="padding:6px 10px;border:0;background:#ef4444;color:#fff;border-radius:8px;cursor:pointer;display:none">Logout</button>
    //   <a href="/profile.html" id="auth-profile-link" style="color:#fff;text-decoration:none;margin-left:6px;display:none">Profile</a>
    // `;
    // document.body.appendChild(bar);
  }

  function injectModal(){
    if (document.getElementById('auth-modal')) return;
    const modal = document.createElement('div');
    modal.id = 'auth-modal';
    modal.style.cssText = 'position:fixed;inset:0;display:none;place-items:center;background:rgba(0,0,0,.4);backdrop-filter:blur(8px);z-index:10001';
    modal.innerHTML = `
      <div style="width:min(92vw,520px);background:rgba(255,255,255,.95);border-radius:16px;padding:24px 22px;box-shadow:0 30px 60px rgba(0,0,0,.25);font:14px Inter,system-ui;position:relative">
        <button id="auth-close" style="position:absolute;top:8px;right:8px;border:0;background:transparent;font-size:22px;cursor:pointer">×</button>
        <div style="display:flex;gap:12px;margin-bottom:16px">
          <button id="tab-login" style="flex:1;padding:10px;border-radius:10px;border:1px solid #d1d5db;background:#f8fafc;cursor:pointer;font-weight:600">Sign in</button>
          <button id="tab-signup" style="flex:1;padding:10px;border-radius:10px;border:1px solid #d1d5db;background:#fff;cursor:pointer;font-weight:600">Sign up</button>
        </div>
        <div id="panel-login">
          <label>Email<input id="login-email" type="email" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <label>Password<input id="login-pass" type="password" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <button id="do-login" style="padding:10px 14px;border:0;background:#2563eb;color:#fff;border-radius:10px;cursor:pointer">Sign in</button>
          <span id="login-err" style="color:#ef4444;margin-left:10px"></span>
        </div>
        <div id="panel-signup" style="display:none">
          <label>Role<select id="su-role" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"><option value="client">Client</option><option value="worker">Worker</option></select></label>
          <label>Name<input id="su-name" type="text" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <label>Email<input id="su-email" type="email" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <label>Phone<input id="su-phone" type="text" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <label>Password<input id="su-pass" type="password" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <label>Repeat password<input id="su-pass2" type="password" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
          <button id="do-signup" style="padding:10px 14px;border:0;background:#2563eb;color:#fff;border-radius:10px;cursor:pointer">Get code to email</button>
          <span id="signup-err" style="color:#ef4444;margin-left:10px"></span>
          <div id="signup-code-step" style="display:none;margin-top:14px;padding-top:12px;border-top:1px dashed #e5e7eb">
            <p style="font-size:13px;color:#4b5563;margin:0 0 8px">We sent a code to your email. Enter it below to finish registration.</p>
            <label>Code from email<input id="su-code" type="text" style="display:block;width:100%;padding:10px;border-radius:10px;border:1px solid #d1d5db;margin:6px 0 12px"/></label>
            <button id="do-signup-confirm" style="padding:10px 14px;border:0;background:#16a34a;color:#fff;border-radius:10px;cursor:pointer">Confirm code</button>
            <span id="signup-code-err" style="color:#ef4444;margin-left:10px"></span>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
  }

  function openModal(tab){
    document.getElementById('auth-modal').style.display = 'grid';
    switchTab(tab||'login');
  }
  function closeModal(){
    document.getElementById('auth-modal').style.display = 'none';
  }
  function switchTab(tab){
    const pl = document.getElementById('panel-login');
    const ps = document.getElementById('panel-signup');
    pl.style.display = tab==='login'?'block':'none';
    ps.style.display = tab==='signup'?'block':'none';
    document.getElementById('tab-login').style.background = tab==='login'?'#fff':'#f8fafc';
    document.getElementById('tab-signup').style.background = tab==='signup'?'#fff':'#f8fafc';
  }

  function bindEvents(){
    document.getElementById('auth-login-btn').onclick = ()=>openModal('login');
    document.getElementById('auth-signup-btn').onclick = ()=>openModal('signup');
    document.getElementById('auth-logout-btn').onclick = async ()=>{ await api.logout(); renderUser(null); };
    document.getElementById('auth-close').onclick = closeModal;
    document.getElementById('tab-login').onclick = ()=>switchTab('login');
    document.getElementById('tab-signup').onclick = ()=>switchTab('signup');
    document.getElementById('do-login').onclick = async ()=>{
      const email = document.getElementById('login-email').value.trim();
      const pass = document.getElementById('login-pass').value;
      const {ok, body} = await api.login(email, pass);
      if (ok){
        document.getElementById('login-err').textContent = '';
        closeModal();
        renderUser(body.user);
      } else {
        const msg = body.error || 'Error';
        document.getElementById('login-err').textContent = msg;
        if (/Аккаунт не найден/i.test(msg)){
          switchTab('signup');
          const suEmail = document.getElementById('su-email');
          if (suEmail && !suEmail.value) suEmail.value = email;
        }
      }
    };
    document.getElementById('do-signup').onclick = async ()=>{
      const role = document.getElementById('su-role').value;
      const name = document.getElementById('su-name').value.trim();
      const email = document.getElementById('su-email').value.trim();
      const phone = document.getElementById('su-phone').value.trim();
      const pass1 = document.getElementById('su-pass').value;
      const pass2 = document.getElementById('su-pass2').value;
      const errEl = document.getElementById('signup-err');
      const codeStep = document.getElementById('signup-code-step');
      const codeErr = document.getElementById('signup-code-err');
      errEl.textContent = '';
      codeErr.textContent = '';
      codeStep.style.display = 'none';
      if (!email || !pass1){
        errEl.textContent = 'Email va parol kiriting';
        return;
      }
      if (pass1 !== pass2){
        errEl.textContent = 'Parollar mos emas';
        return;
      }
      const payload = { role, name, email, phone, password: pass1 };
      const {ok, body} = await api.signup(payload);
      if (!ok){
        errEl.textContent = body.error || 'Error';
        return;
      }
      codeStep.style.display = 'block';
    };
    const confirmBtn = document.getElementById('do-signup-confirm');
    if (confirmBtn){
      confirmBtn.onclick = async ()=>{
        const email = document.getElementById('su-email').value.trim();
        const code = document.getElementById('su-code').value.trim();
        const codeErr = document.getElementById('signup-code-err');
        codeErr.textContent = '';
        if (!email || !code){
          codeErr.textContent = 'Email va kodni kiriting';
          return;
        }
        const {ok, body} = await api.signupConfirm(email, code);
        if (!ok){
          codeErr.textContent = body.error || 'Error';
          return;
        }
        closeModal();
        renderUser(body.user);
      };
    }
  }

  function renderUser(user){
    const u = document.getElementById('auth-user');
    const loginBtn = document.getElementById('auth-login-btn');
    const signupBtn = document.getElementById('auth-signup-btn');
    const logoutBtn = document.getElementById('auth-logout-btn');
    const profile = document.getElementById('auth-profile-link');
    if (user){
      u.textContent = `${user.name} (${user.role})`;
      loginBtn.style.display = 'none';
      signupBtn.style.display = 'none';
      logoutBtn.style.display = 'inline-block';
      profile.style.display = 'inline-block';
    } else {
      u.textContent = 'Guest';
      loginBtn.style.display = 'inline-block';
      signupBtn.style.display = 'inline-block';
      logoutBtn.style.display = 'none';
      profile.style.display = 'none';
    }
  }

  async function init(){
    injectAuthUI();
    injectModal();
    bindEvents();
    try{
      const data = await api.me();
      renderUser(data.user);
    }catch(e){ renderUser(null); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
