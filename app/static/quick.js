"use strict";
// One-tap quick links: the page submits itself, so a person's tap logs care
// while chat-app link previews (which don't run scripts) never change anything.
(function () {
  const main = document.querySelector("main[data-auto]");
  const form = main && main.querySelector("form");
  if (!form) return;
  const status = document.getElementById("quick-status");
  if (status) status.textContent = "Logging…";
  const button = form.querySelector("button");
  if (button) button.disabled = true;
  form.submit();
})();
