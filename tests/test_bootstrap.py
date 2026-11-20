{% extends 'base.html' %}{% block title %}Iniciar sesión{% endblock %}
{% block content_public %}
<div class="login-layout">
<section class="login-showcase">
<div class="showcase-top"><span class="login-showcase-mark"><svg class="icon"><use href="#i-grid"/></svg></span><span><strong>RadicaCuentas</strong><small>Una sola plataforma. Todos tus procesos.</small></span></div>
<div class="showcase-graphic"><div class="graphic-orbit orbit-1"></div><div class="graphic-orbit orbit-2"></div><div class="graphic-card card-one"><span class="mini-circle"><svg class="icon"><use href="#i-check"/></svg></span><span>Registro recibido<small>Confirmación en tiempo real</small></span></div><div class="graphic-card card-two"><span class="mini-circle purple"><svg class="icon"><use href="#i-shield"/></svg></span><span>Accesos personalizados<small>Control por formulario</small></span></div><div class="graphic-center"><svg class="icon"><use href="#i-grid"/></svg></div></div>
<div class="showcase-bottom"><div class="showcase-eyebrow">GESTIÓN INSTITUCIONAL UNIFICADA</div><h1>Más claridad.<br/>Menos trámites.<br/><em>Todo en un lugar.</em></h1><p>Radicación, supervisión, seguimiento y control, con trazabilidad y permisos por persona.</p></div>
</section>
<section class="login-form-side"><div class="login-form-wrap"><div class="mobile-login-brand"><span class="brandmark">R</span><strong>RadicaCuentas</strong></div><div class="eyebrow">ACCESO A TU ESPACIO</div><h1>Bienvenido de nuevo</h1><p>Ingresa con el correo y la contraseña de tu cuenta autorizada.</p>
{% if request.query_params.get('activado') %}<div class="alert success">Cuenta activada. Ya puedes ingresar.</div>{% endif %}
{% if request.query_params.get('configurado') %}<div class="alert success">Administrador creado. Ingresa para configurar tu equipo.</div>{% endif %}
{% if request.query_params.get('sesion') %}<div class="alert">Tu sesión no está activa. Ingresa para continuar.</div>{% endif %}
{% if request.query_params.get('configuracion') %}<div class="alert success">El administrador ya está creado. Ingresa con su cuenta.</div>{% endif %}
{% if error %}<div class="alert error">{{ error }}</div>{% endif %}
<form method="post" action="/ingresar"><input type="hidden" name="csrf" value="{{ csrf }}"/>
<div class="field"><label for="login-email">Correo electrónico</label><div class="input-with-icon"><svg class="icon"><use href="#i-mail"/></svg><input id="login-email" name="email" type="email" autocomplete="username" required placeholder="usuario@entidad.gov.co"/></div></div>
<div class="field"><label for="login-password">Contraseña</label><div class="input-with-icon"><svg class="icon"><use href="#i-lock"/></svg><input id="login-password" name="password" type="password" autocomplete="current-password" required placeholder="Tu contraseña"/></div></div>
<button class="btn primary full login-submit" type="submit">Entrar al aplicativo <svg class="icon"><use href="#i-arrow"/></svg></button></form>
{% if setup_available %}<a class="setup-link" href="/configuracion-inicial">Configurar el primer administrador</a>{% endif %}
<div class="login-note"><svg class="icon"><use href="#i-shield"/></svg><span>Tu cuenta y tus permisos son administrados por el responsable del sistema.</span></div>
</div></section>
</div>{% endblock %}
