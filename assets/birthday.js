/* Sees Journal — Birthday Artist of the Day.
   Picks one artist per day, entirely client-side from the visitor's local date, so the feature
   changes every day no matter when the page was last built. If any artist's birthday (month-day)
   matches today, that artist is featured ("Born on this day"); otherwise an artist is chosen by
   day-of-year so the slot is never empty and still rotates daily.

   Images: the portrait is fetched from the artist's English Wikipedia summary and shown ONLY when
   the image is served from Wikimedia Commons (i.e. verifiably free-licensed). Non-free images and
   the copyrighted artworks themselves are never embedded — the card links out to view the work. */
(function () {
  "use strict";
  var dataEl = document.getElementById("bday-data");
  var card = document.getElementById("bday-card");
  if (!dataEl || !card) return;

  var artists;
  try {
    // the build step fills this block between HTML-comment markers; strip them before parsing
    artists = JSON.parse(dataEl.textContent.replace(/<!--[\s\S]*?-->/g, "").trim() || "[]");
  } catch (e) { return; }
  if (!Array.isArray(artists) || !artists.length) return;

  var MONTHS = ["January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"];

  var now = new Date();
  var mm = String(now.getMonth() + 1).padStart(2, "0");
  var dd = String(now.getDate()).padStart(2, "0");
  var md = mm + "-" + dd;
  var year = now.getFullYear();
  // day-of-year (1..366), used as the rotation index on non-birthday days
  var doy = Math.floor((now - new Date(year, 0, 0)) / 86400000);

  // Birthday match takes priority; ties on a shared day rotate year over year.
  var bdayMatches = artists.filter(function (a) { return (a.dob || "").slice(5) === md; });
  var artist, isBirthday;
  if (bdayMatches.length) {
    // Priority: contemporary/modern (no era) > classic (Old Masters) > fill (added to cover
    // empty days). The highest-priority tier present today headlines; within a tier, rotate by year.
    var rank = function (a) { return a.era === "fill" ? 2 : (a.era === "classic" ? 1 : 0); };
    var best = Math.min.apply(null, bdayMatches.map(rank));
    var pool = bdayMatches.filter(function (a) { return rank(a) === best; });
    artist = pool[year % pool.length];
    isBirthday = true;
  } else {
    // Non-birthday days: rotate among artists with reliably free imagery (the classic masters all
    // have public-domain portraits AND works on Commons), so the slot always shows a picture rather
    // than a bare monogram. Contemporary artists still headline their own birthdays above.
    var imgPool = artists.filter(function (a) { return a.era === "classic"; });
    if (!imgPool.length) imgPool = artists;
    artist = imgPool[doy % imgPool.length];
    isBirthday = false;
  }

  function txt(id, value) { var n = document.getElementById(id); if (n) n.textContent = value || ""; }

  var p = (artist.dob || "").split("-");
  var prettyDate = (p.length === 3)
    ? MONTHS[parseInt(p[1], 10) - 1] + " " + parseInt(p[2], 10) + ", " + p[0]
    : (artist.dob || "");

  var todayStr = MONTHS[now.getMonth()] + " " + now.getDate() + ", " + year;
  txt("bday-kicker", todayStr);                       // the eyebrow always shows today's real date
  var heading = document.getElementById("bday-h");     // only claim "Born on This Day" when it's true
  if (heading) heading.textContent = isBirthday ? "Born on This Day" : "Artist of the Day";
  txt("bday-name", artist.name);
  txt("bday-when", "Born " + prettyDate);
  txt("bday-bio", artist.bio);
  txt("bday-work", artist.work ? ("\u201C" + artist.work + "\u201D") : "");
  txt("bday-workmeta", [artist.year, artist.medium, artist.inst].filter(Boolean).join("  \u00B7  "));

  // Monogram fallback for the portrait
  var initials = (artist.name || "").split(/\s+/)
    .map(function (w) { return w.charAt(0); }).join("").slice(0, 2).toUpperCase();
  txt("bday-initials", initials);

  // Links: about the artist + a search that lands on the work (never embeds the artwork)
  var links = document.getElementById("bday-links");
  if (links) {
    var title = (artist.wiki || artist.name || "").replace(/ /g, "_");
    var aboutUrl = "https://en.wikipedia.org/wiki/" + encodeURIComponent(title);
    var workUrl = "https://en.wikipedia.org/w/index.php?search=" +
      encodeURIComponent((artist.work || "") + " " + (artist.name || ""));
    links.innerHTML = "";
    var a1 = document.createElement("a");
    a1.href = aboutUrl; a1.target = "_blank"; a1.rel = "noopener noreferrer";
    a1.className = "bday-link"; a1.textContent = "About the artist \u2197";
    var a2 = document.createElement("a");
    a2.href = workUrl; a2.target = "_blank"; a2.rel = "noopener noreferrer";
    a2.className = "bday-link bday-link--ghost"; a2.textContent = "See the work \u2197";
    links.appendChild(a1); links.appendChild(a2);
  }

  // ---------- Images: only ever display a Wikimedia Commons (public-domain or freely-licensed)
  //            image, always shown with its author + license credit fetched live from Commons. ----------
  function isCommons(src) { return !!src && src.indexOf("/commons/") !== -1; }
  // bump a Commons thumb URL (".../320px-Name.jpg") to a larger render for crisper display
  function upsize(src, w) { return src.replace(/\/\d+px-/, "/" + w + "px-"); }
  function summary(title) {
    return fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" +
      encodeURIComponent((title || "").replace(/ /g, "_")))
      .then(function (r) { return r.ok ? r.json() : null; });
  }
  // Load via a preloader so the background (and the monogram-hiding class) is only applied once the
  // image actually loads — a 404 (e.g. a thumbnail wider than the original) then falls back to the
  // original size instead of leaving a blank panel. Credit is shown only on a successful load.
  function setPortrait(src, fallback, file) {
    var portrait = document.getElementById("bday-portrait");
    if (!portrait || !src) return;
    var im = new Image();
    im.onload = function () {
      portrait.style.backgroundImage = 'url("' + src + '")';
      portrait.classList.add("has-img");
      if (file) showCredit(document.getElementById("bday-portrait-credit"), file);
    };
    im.onerror = function () { if (fallback && fallback !== src) setPortrait(fallback, null, file); };
    im.src = src;
  }
  // Derive the "File:Name.ext" page title from a Commons image URL (thumb or original form).
  function commonsFile(src) {
    if (!src) return null;
    var m = src.match(/\/commons\/(?:thumb\/)?[0-9a-f]\/[0-9a-f]{2}\/([^\/]+?)(?:\/\d+px-[^\/]*)?$/i);
    return m ? "File:" + decodeURIComponent(m[1]) : null;
  }
  // extmetadata Artist is HTML; render it to plain text.
  function plain(html) { var d = document.createElement("div"); d.innerHTML = html || ""; return (d.textContent || "").replace(/\s+/g, " ").trim(); }
  // Fetch author + license for a Commons file and render the required attribution into `el`.
  function showCredit(el, file) {
    if (!el || !file) return;
    fetch("https://commons.wikimedia.org/w/api.php?action=query&format=json&origin=*&prop=imageinfo&iiprop=extmetadata&titles=" +
      encodeURIComponent(file))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var pages = j && j.query && j.query.pages; if (!pages) return;
        var info = pages[Object.keys(pages)[0]].imageinfo;
        var md = info && info[0] && info[0].extmetadata; if (!md) return;
        var author = plain(md.Artist && md.Artist.value) || "Unknown author";
        var license = plain(md.LicenseShortName && md.LicenseShortName.value);
        var a = document.createElement("a");
        a.href = "https://commons.wikimedia.org/wiki/" + encodeURIComponent(file);
        a.target = "_blank"; a.rel = "noopener noreferrer";
        a.textContent = author + (license ? " \u00B7 " + license : "") + " \u00B7 Wikimedia Commons";
        el.innerHTML = ""; el.appendChild(a); el.style.display = "block";
      }).catch(function () {});
  }
  // For a non-free image (e.g. a fair-use file Wikipedia hosts locally, not on Commons): show a
  // short "may be subject to copyright" notice linking to the source article instead of a license.
  function showCopyrightNote(el, href) {
    if (!el) return;
    var node;
    if (href) {
      node = document.createElement("a");
      node.href = href; node.target = "_blank"; node.rel = "noopener noreferrer";
      node.textContent = "Image may be subject to copyright";
    } else {
      node = document.createTextNode("Image may be subject to copyright");
    }
    el.innerHTML = ""; el.appendChild(node); el.style.display = "block";
  }

  // Portrait: prefer the article's Commons lead image; else fall back to the artist's Wikidata
  // "image" (P18), which is always a free Commons file. The monogram stays if neither exists.
  try {
    summary(artist.wiki || artist.name).then(function (j) {
      if (!j) return;
      var src = j.thumbnail && j.thumbnail.source;
      if (isCommons(src)) {
        setPortrait(upsize(src, 640), src, commonsFile(src));
      } else if (j.wikibase_item) {
        fetch("https://www.wikidata.org/w/api.php?action=wbgetclaims&format=json&origin=*&property=P18&entity=" +
          encodeURIComponent(j.wikibase_item))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (w) {
            var c = w && w.claims && w.claims.P18 && w.claims.P18[0];
            var file = c && c.mainsnak && c.mainsnak.datavalue && c.mainsnak.datavalue.value;
            if (file) {
              var enc = encodeURIComponent(file.replace(/ /g, "_"));
              setPortrait("https://commons.wikimedia.org/wiki/Special:FilePath/" + enc + "?width=640",
                          "https://commons.wikimedia.org/wiki/Special:FilePath/" + enc, "File:" + file);
            }
          }).catch(function () {});
      }
    }).catch(function () {});
  } catch (e) { /* offline — monogram stays */ }

  // Signature work image. (a) artist.workimg — a specific image the publisher has chosen to embed
  // directly (e.g. a copyrighted cover); shown with a short "may be subject to copyright" notice
  // linking to the source. (b) artist.workwiki — a public-domain work; shown only when it is
  // Commons-hosted (free) AND the article text names the artist, with full author/license credit.
  try {
    var workImg = document.getElementById("bday-workimg");
    if (artist.workimg && workImg) {
      workImg.alt = artist.work || "";
      workImg.onload = function () {
        workImg.hidden = false;
        var cr = document.getElementById("bday-work-credit");
        if (cr) {
          cr.innerHTML = "";
          var note;
          if (artist.workimgsrc) {
            note = document.createElement("a");
            note.href = artist.workimgsrc;
            note.target = "_blank"; note.rel = "noopener noreferrer";
            note.textContent = "Image may be subject to copyright";
          } else {
            note = document.createTextNode("Image may be subject to copyright");
          }
          cr.appendChild(note); cr.style.display = "block";
        }
      };
      workImg.onerror = function () { workImg.hidden = true; };   // a miss leaves the link only
      workImg.src = artist.workimg;
    } else if (artist.workwiki) {
      var surname = (artist.name || "").split(/\s+/).pop().toLowerCase();
      summary(artist.workwiki).then(function (j) {
        if (!j) return;
        var src = j.thumbnail && j.thumbnail.source;
        var blurb = ((j.description || "") + " " + (j.extract || "")).toLowerCase();
        // Show the work's lead image when the article is genuinely about this artist's piece
        // (its text names the artist). Free Commons images get a full author/license credit;
        // non-free images (e.g. fair-use files Wikipedia hosts) get a "may be copyrighted" notice.
        if (src && surname && blurb.indexOf(surname) !== -1) {
          var img = document.getElementById("bday-workimg");
          if (!img) return;
          var commons = isCommons(src);
          var page = j.content_urls && j.content_urls.desktop && j.content_urls.desktop.page;
          var triedFallback = false;
          img.alt = artist.work || "";
          img.onload = function () {
            img.hidden = false;
            if (commons) showCredit(document.getElementById("bday-work-credit"), commonsFile(src));
            else showCopyrightNote(document.getElementById("bday-work-credit"), page);
          };
          img.onerror = function () {
            if (!triedFallback) { triedFallback = true; img.src = src; }   // retry at the original size
            else { img.hidden = true; }                                    // give up -> the link stays
          };
          img.src = commons ? upsize(src, 800) : src;   // don't upscale a non-free thumbnail
        }
      }).catch(function () {});
    }
  } catch (e) { /* offline — link stays */ }
})();
