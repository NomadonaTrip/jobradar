// ── State ──

let currentStep = 0;
const totalSteps = 6;
const tags = { roles: [], locations: [], exclude: [] };
let resumeFileData = "";
let resumeFileName = "";
const _formLoadedAt = Date.now();

// ── XSS Prevention ──

function escapeHtml(str) {
  const div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}

// ── Client-Side Validation (Layer 5) ──

function validateStep(step) {
  const errorId = `stepError${step}`;
  const errorEl = document.getElementById(errorId);
  if (!errorEl) return true;

  let msg = "";

  if (step === 1) {
    const firstName = document.getElementById("firstName").value.trim();
    const lastName = document.getElementById("lastName").value.trim();
    const email = document.getElementById("email").value.trim();
    if (!firstName || !lastName) {
      msg = "First name and last name are required.";
    } else if (!email) {
      msg = "Email address is required.";
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      msg = "Please enter a valid email address.";
    }
  } else if (step === 2) {
    const resumeText = document.getElementById("resumeText").value.trim();
    if (!resumeText && !resumeFileData) {
      msg = "Please upload a resume file or paste your resume text.";
    }
  } else if (step === 3) {
    if (tags.roles.length === 0) {
      msg = "Please add at least one target role.";
    }
  }

  if (msg) {
    errorEl.textContent = msg;
    errorEl.classList.add("visible");
    return false;
  }
  errorEl.textContent = "";
  errorEl.classList.remove("visible");
  return true;
}

// ── Navigation ──

function nextStep() {
  if (currentStep >= 1 && currentStep <= 3 && !validateStep(currentStep)) {
    return;
  }
  if (currentStep === 4) buildReview();
  if (currentStep < totalSteps) {
    document
      .querySelector(`.step[data-step="${currentStep}"]`)
      .classList.remove("active");
    currentStep++;
    const next = document.querySelector(`.step[data-step="${currentStep}"]`);
    next.classList.remove("active");
    void next.offsetWidth; // reflow for animation
    next.classList.add("active");
    updateProgress();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function prevStep() {
  if (currentStep > 0) {
    document
      .querySelector(`.step[data-step="${currentStep}"]`)
      .classList.remove("active");
    currentStep--;
    const prev = document.querySelector(`.step[data-step="${currentStep}"]`);
    prev.classList.remove("active");
    void prev.offsetWidth;
    prev.classList.add("active");
    updateProgress();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function updateProgress() {
  document.querySelectorAll(".progress-dot").forEach((dot) => {
    const step = parseInt(dot.dataset.step);
    dot.classList.toggle("active", step === currentStep);
    dot.classList.toggle("done", step < currentStep);
  });
  document.getElementById("stepCounter").textContent = Math.min(
    currentStep + 1,
    totalSteps,
  );
  document.body.classList.toggle("hero-mode", currentStep === 0);
}

// ── Tags ──

function handleTagKey(e, group) {
  if (e.key === "Enter" && e.target.value.trim()) {
    e.preventDefault();
    const val = e.target.value.trim();
    if (!tags[group].includes(val)) {
      tags[group].push(val);
      renderTags(group);
    }
    e.target.value = "";
  }
}

function renderTags(group) {
  const wrap = document.getElementById(`${group}Wrap`);
  const input = document.getElementById(`${group}Input`);
  wrap.querySelectorAll(".tag").forEach((t) => t.remove());
  tags[group].forEach((tag, i) => {
    const el = document.createElement("span");
    el.className = "tag";
    const textNode = document.createTextNode(tag);
    el.appendChild(textNode);
    const btn = document.createElement("button");
    btn.innerHTML = "&times;";
    btn.onclick = () => removeTag(group, i);
    el.appendChild(btn);
    wrap.insertBefore(el, input);
  });
}

function removeTag(group, index) {
  tags[group].splice(index, 1);
  renderTags(group);
}

// ── File upload: resume ──

function handleFileUpload(input) {
  if (input.files.length) {
    const file = input.files[0];
    document.getElementById("uploadZone").classList.add("has-file");
    document.getElementById("uploadText").innerHTML =
      `<strong>${escapeHtml(file.name)}</strong><br><span class="file-types">${(file.size / 1024).toFixed(0)} KB &mdash; Click to change</span>`;

    if (file.name.endsWith(".txt") || file.name.endsWith(".md")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById("resumeText").value = e.target.result;
      };
      reader.readAsText(file);
      resumeFileData = "";
      resumeFileName = "";
    } else if (
      file.name.endsWith(".pdf") ||
      file.name.endsWith(".docx") ||
      file.name.endsWith(".doc")
    ) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const dataUrl = e.target.result;
        resumeFileData = dataUrl.split(",")[1];
        resumeFileName = file.name;
      };
      reader.readAsDataURL(file);
    }
  }
}

function initDragDrop(zoneId, fileInputId, handler) {
  const zone = document.getElementById(zoneId);
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.style.borderColor = "var(--gold)";
  });
  zone.addEventListener("dragleave", () => {
    zone.style.borderColor = "";
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.style.borderColor = "";
    if (e.dataTransfer.files.length) {
      document.getElementById(fileInputId).files = e.dataTransfer.files;
      handler(document.getElementById(fileInputId));
    }
  });
}

// ── File upload: cover letter ──

function handleCoverLetterUpload(input) {
  if (input.files.length) {
    const file = input.files[0];
    document.getElementById("clUploadZone").classList.add("has-file");
    document.getElementById("clUploadText").innerHTML =
      `<strong>${escapeHtml(file.name)}</strong><br><span class="file-types">${(file.size / 1024).toFixed(0)} KB &mdash; Click to change</span>`;

    if (file.name.endsWith(".txt") || file.name.endsWith(".md")) {
      const reader = new FileReader();
      reader.onload = (e) => {
        document.getElementById("coverLetterText").value = e.target.result;
      };
      reader.readAsText(file);
    }
  }
}

// ── Review builder (XSS-safe) ──

function buildReview() {
  const data = gatherData();
  const e = escapeHtml;
  let html = "";

  html += `<div class="review-section">
      <h3>Contact Information</h3>
      <div class="review-row"><span class="rlabel">Name</span><span class="rvalue">${e(data.firstName)} ${e(data.lastName)}</span></div>
      <div class="review-row"><span class="rlabel">Email</span><span class="rvalue">${e(data.email)}</span></div>
      <div class="review-row"><span class="rlabel">Phone</span><span class="rvalue">${data.phone ? e(data.phone) : "\u2014"}</span></div>
      <div class="review-row"><span class="rlabel">Location</span><span class="rvalue">${e(data.location)}</span></div>
      <div class="review-row"><span class="rlabel">LinkedIn</span><span class="rvalue">${data.linkedin ? e(data.linkedin) : "\u2014"}</span></div>
  </div>`;

  const resumeDisplay = data.resumeText
    ? e(data.resumeText.substring(0, 100)) + "..."
    : data.resumeFileData
      ? "File uploaded: " + e(data.resumeFileName)
      : "Not provided";
  const clDisplay = data.coverLetterText
    ? e(data.coverLetterText.substring(0, 100)) + "..."
    : "Not provided";

  html += `<div class="review-section">
      <h3>Resume</h3>
      <div class="review-row"><span class="rlabel">Content</span><span class="rvalue">${resumeDisplay}</span></div>
      <div class="review-row"><span class="rlabel">Cover Letter</span><span class="rvalue">${clDisplay}</span></div>
  </div>`;

  html += `<div class="review-section">
      <h3>Search Preferences</h3>
      <div class="review-row"><span class="rlabel">Target Roles</span><span class="rvalue"><div class="review-tags">${data.roles.map((r) => `<span class="review-tag">${e(r)}</span>`).join("")}</div></span></div>
      <div class="review-row"><span class="rlabel">Locations</span><span class="rvalue"><div class="review-tags">${data.locations.map((l) => `<span class="review-tag">${e(l)}</span>`).join("")}</div></span></div>
      <div class="review-row"><span class="rlabel">Min Salary</span><span class="rvalue">${data.minSalary ? e(data.minSalary) : "No minimum"}</span></div>
      <div class="review-row"><span class="rlabel">Arrangement</span><span class="rvalue">${e(data.workArrangement)}</span></div>
      ${data.exclude.length ? `<div class="review-row"><span class="rlabel">Excluded</span><span class="rvalue"><div class="review-tags">${data.exclude.map((x) => `<span class="review-tag">${e(x)}</span>`).join("")}</div></span></div>` : ""}
  </div>`;

  html += `<div class="review-section">
      <h3>Experience Discovery</h3>
      ${data.discovery.teamSize ? `<div class="review-row"><span class="rlabel">Team Size</span><span class="rvalue">${e(data.discovery.teamSize)}</span></div>` : ""}
      ${data.discovery.budget ? `<div class="review-row"><span class="rlabel">Largest Budget</span><span class="rvalue">${e(data.discovery.budget)}</span></div>` : ""}
      ${data.discovery.certs ? `<div class="review-row"><span class="rlabel">Certifications</span><span class="rvalue">${e(data.discovery.certs)}</span></div>` : ""}
      ${data.discovery.tools ? `<div class="review-row"><span class="rlabel">Tools</span><span class="rvalue">${e(data.discovery.tools)}</span></div>` : ""}
      ${data.discovery.industries ? `<div class="review-row"><span class="rlabel">Industries</span><span class="rvalue">${e(data.discovery.industries)}</span></div>` : ""}
  </div>`;

  document.getElementById("reviewContent").innerHTML = html;
}

// ── Data gathering ──

function gatherData() {
  return {
    firstName: document.getElementById("firstName").value.trim(),
    lastName: document.getElementById("lastName").value.trim(),
    email: document.getElementById("email").value.trim(),
    phone: document.getElementById("phone").value.trim(),
    location: document.getElementById("location").value.trim(),
    linkedin: document.getElementById("linkedin").value.trim(),
    resumeText: document.getElementById("resumeText").value.trim(),
    resumeFileName: resumeFileName,
    resumeFileData: resumeFileData,
    coverLetterText: document.getElementById("coverLetterText").value.trim(),
    roles: tags.roles,
    locations: tags.locations,
    exclude: tags.exclude,
    minSalary: document.getElementById("minSalary").value.trim(),
    workArrangement: document.getElementById("workArrangement").value,
    prefNotes: document.getElementById("prefNotes").value.trim(),
    discovery: {
      teamSize: document.getElementById("d_teamSize").value.trim(),
      budget: document.getElementById("d_budget").value.trim(),
      metrics: document.getElementById("d_metrics").value.trim(),
      certs: document.getElementById("d_certs").value.trim(),
      challenge: document.getElementById("d_challenge").value.trim(),
      tools: document.getElementById("d_tools").value.trim(),
      industries: document.getElementById("d_industries").value.trim(),
      hidden: document.getElementById("d_hidden").value.trim(),
    },
    submittedAt: new Date().toISOString(),
    _hp: document.getElementById("website").value,
    _elapsed: Date.now() - _formLoadedAt,
  };
}

// ── Apps Script endpoint ──
// Replace this URL after deploying your Google Apps Script web app
const APPS_SCRIPT_URL =
  "https://script.google.com/macros/s/AKfycbz2m150WPtCbYW40GJ_2Y8kE16gxN0ezfQ7W6n-EJGdpiT7fXIYxt63ogNdFm6v11BY/exec";

// ── Submit ──

function submitOnboarding() {
  const data = gatherData();
  const btn = document.getElementById("submitBtn");
  btn.disabled = true;
  btn.textContent = "Submitting...";

  fetch(APPS_SCRIPT_URL, {
    method: "POST",
    mode: "no-cors",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
    .then(() => {
      nextStep();
    })
    .catch((err) => {
      console.error("Submission error:", err);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download =
        `onboarding_${data.firstName}_${data.lastName}_${Date.now()}.json`.replace(
          /\s+/g,
          "_",
        );
      a.click();
      URL.revokeObjectURL(url);
      nextStep();
    });
}

// ── Init drag & drop on load ──

document.addEventListener("DOMContentLoaded", () => {
  initDragDrop("uploadZone", "resumeFile", handleFileUpload);
  initDragDrop("clUploadZone", "coverLetterFile", handleCoverLetterUpload);
});
