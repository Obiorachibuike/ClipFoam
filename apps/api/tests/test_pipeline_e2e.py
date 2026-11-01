"""The principal acceptance test: upload → transcribe/import → highlights →
select → edit → captions → render → download."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import SRT_SAMPLE, run_jobs


def test_health_endpoint(client: TestClient):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["database"]["ok"] is True
    assert body["ffmpeg"] is True


def test_upload_ingest_extracts_real_metadata(client, auth, project, sample_video):
    with sample_video.open("rb") as handle:
        response = client.post(
            f"/api/v1/media/projects/{project['id']}/upload",
            files={"file": ("sample.mp4", handle, "video/mp4")},
            headers=auth,
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job"]["type"] == "media.ingest"

    run_jobs()
    media = client.get(f"/api/v1/media/{body['media']['id']}", headers=auth).json()
    assert media["status"] == "ready"
    assert media["width"] == 640 and media["height"] == 360
    assert 23.0 < media["duration_seconds"] < 25.0
    assert media["has_audio"] is True
    assert media["fps"] and 29.0 < media["fps"] < 31.0
    assert media["thumbnail_url"], "thumbnail should be generated and signed"


def test_full_acceptance_workflow(client, auth, project, transcribed_media):
    project_id = project["id"]
    media_id = transcribed_media["id"]

    # 1. Transcript is stored with real timestamps derived from the file.
    transcript = client.get(f"/api/v1/media/{media_id}/transcript", headers=auth).json()
    assert transcript["status"] == "ready"
    assert len(transcript["segments"]) == 5
    assert transcript["segments"][0]["start_seconds"] == 0.5
    assert transcript["is_manual"] is True
    original = transcript["segments"][0]["text"]

    # 2. Edit a timestamped segment and confirm the original is preserved.
    segment_id = transcript["segments"][0]["id"]
    patched = client.patch(
        f"/api/v1/transcripts/{transcript['id']}/segments/{segment_id}",
        json={"text": original.replace("Most people", "Most creators")},
        headers=auth,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["is_edited"] is True
    assert patched.json()["original_text"] == original

    # 3. Highlight detection (deterministic mode, clearly labelled).
    response = client.post(
        f"/api/v1/projects/{project_id}/candidates/generate",
        json={"clip_count": 3, "min_duration": 12, "max_duration": 30, "use_ai": False},
        headers=auth,
    )
    assert response.status_code == 202, response.text
    run_jobs()
    candidates = client.get(f"/api/v1/projects/{project_id}/candidates", headers=auth).json()
    assert candidates, "candidates should be produced from the transcript"
    top = candidates[0]
    assert top["algorithm"] == "deterministic"
    assert top["start_seconds"] >= 0
    assert top["end_seconds"] > top["start_seconds"]
    assert top["excerpt"].strip(), "excerpt must be grounded in the transcript"
    assert top["score"] > 0
    assert top["score_breakdown"]

    # 4. Approve the top candidate -> clip.
    clip_response = client.post(f"/api/v1/candidates/{top['id']}/approve", headers=auth)
    assert clip_response.status_code == 201, clip_response.text
    clip = clip_response.json()
    assert clip["status"] == "draft"
    assert clip["start_seconds"] == top["start_seconds"]

    # 5. Edit the clip via transcript-based selection.
    second = candidates[1] if len(candidates) > 1 else candidates[0]
    segment_ids = [
        seg["id"]
        for seg in transcript["segments"]
        if seg["end_seconds"] > second["start_seconds"] and seg["start_seconds"] < second["end_seconds"]
    ]
    trimmed = client.post(
        f"/api/v1/clips/{clip['id']}/range-from-segments",
        json=segment_ids,
        headers=auth,
    )
    assert trimmed.status_code == 200, trimmed.text
    clip = trimmed.json()

    # 6. Captions: adjust style, preview, export SRT.
    style = {
        key: value
        for key, value in {
            "preset": "karaoke",
            "font_size": 64,
            "uppercase": True,
            "words_per_caption": 3,
            "animation": "karaoke",
        }.items()
    }
    update = client.patch(
        f"/api/v1/clips/{clip['id']}/captions",
        json={"enabled": True, "style": style},
        headers=auth,
    )
    assert update.status_code == 200, update.text
    assert update.json()["preset"] == "karaoke"
    preview = client.get(f"/api/v1/clips/{clip['id']}/captions/preview", headers=auth).json()
    assert preview["lines"], "caption preview must contain lines"
    assert preview["lines"][0]["start"] >= 0
    srt = client.get(f"/api/v1/clips/{clip['id']}/captions/export?format=srt", headers=auth)
    assert srt.status_code == 200
    assert "-->" in srt.text

    # 7. Render the MP4.
    render = client.post(
        f"/api/v1/clips/{clip['id']}/render",
        json={"settings": {"resolution": "720p", "quality": "draft", "captions_enabled": True}},
        headers=auth,
    )
    assert render.status_code == 202, render.text
    job_id = render.json()["id"]
    run_jobs()

    job = client.get(f"/api/v1/jobs/{job_id}", headers=auth).json()
    assert job["status"] == "completed", job
    exports = client.get(f"/api/v1/clips/{clip['id']}/exports", headers=auth).json()
    videos = [export for export in exports if export["kind"] == "video"]
    assert videos, "a video export must exist"
    export = videos[0]
    assert export["status"] == "ready"
    assert export["size_bytes"] > 10_000
    assert export["width"] == 720 and export["height"] == 1280

    # 8. Download through a signed URL and confirm the bytes are a real MP4.
    link = client.get(f"/api/v1/exports/{export['id']}/download", headers=auth)
    assert link.status_code == 200, link.text
    url = link.json()["url"]
    download = client.get(url)
    assert download.status_code == 200
    content = download.content
    assert len(content) > 10_000
    assert b"ftyp" in content[:32], "downloaded file must be a valid MP4 container"

    # 9. Unsigned access is rejected.
    unsigned = client.get(url.split("?")[0])
    assert unsigned.status_code == 403

    # 10. Content studio works without any paid AI key (deterministic mode).
    content = client.post(
        f"/api/v1/clips/{clip['id']}/content/generate",
        json={"kinds": ["title", "hashtags", "description"], "count": 2},
        headers=auth,
    )
    assert content.status_code == 201, content.text
    items = content.json()
    assert items
    assert all(item["provider"] == "deterministic" for item in items)


def test_generated_transcript_srt_matches_source(client, auth, transcribed_media):
    response = client.get(
        f"/api/v1/media/{transcribed_media['id']}/transcript/export?format=srt", headers=auth
    )
    assert response.status_code == 200
    body = response.text
    assert body.strip() == SRT_SAMPLE.strip()


def test_rerender_does_not_retranscribe(client, auth, project, transcribed_media):
    transcript = client.get(
        f"/api/v1/media/{transcribed_media['id']}/transcript", headers=auth
    ).json()
    before = transcript["updated_at"]

    response = client.post(
        f"/api/v1/projects/{project['id']}/candidates/generate",
        json={"clip_count": 2, "min_duration": 10, "max_duration": 30},
        headers=auth,
    )
    assert response.status_code == 202
    run_jobs()
    candidates = client.get(f"/api/v1/projects/{project['id']}/candidates", headers=auth).json()
    clip = client.post(f"/api/v1/candidates/{candidates[0]['id']}/approve", headers=auth).json()
    first = client.post(f"/api/v1/clips/{clip['id']}/render", json={}, headers=auth)
    run_jobs()
    second = client.post(f"/api/v1/clips/{clip['id']}/render", json={"settings": {"quality": "high"}}, headers=auth)
    run_jobs()
    assert first.status_code == 202 and second.status_code == 202
    exports = client.get(f"/api/v1/clips/{clip['id']}/exports", headers=auth).json()
    assert len([item for item in exports if item["kind"] == "video"]) == 2
    after = client.get(f"/api/v1/media/{transcribed_media['id']}/transcript", headers=auth).json()
    assert after["updated_at"] == before, "rendering must never re-transcribe the source"


def test_render_validation_rejects_bad_window(client, auth, project, transcribed_media):
    response = client.post(
        f"/api/v1/projects/{project['id']}/clips",
        json={"start_seconds": 10, "end_seconds": 5},
        headers=auth,
    )
    assert response.status_code in {400, 422}


def test_transcript_export_vtt(client, auth, transcribed_media):
    response = client.get(
        f"/api/v1/media/{transcribed_media['id']}/transcript/export?format=vtt", headers=auth
    )
    assert response.status_code == 200
    assert response.text.startswith("WEBVTT")


def test_download_link_for_finished_export(client, auth, project, transcribed_media):
    candidates = client.post(
        f"/api/v1/projects/{project['id']}/candidates/generate",
        json={"clip_count": 1, "min_duration": 10, "max_duration": 30},
        headers=auth,
    )
    assert candidates.status_code == 202
    run_jobs()
    candidate = client.get(f"/api/v1/projects/{project['id']}/candidates", headers=auth).json()[0]
    clip = client.post(f"/api/v1/candidates/{candidate['id']}/approve", headers=auth).json()
    client.post(f"/api/v1/clips/{clip['id']}/render", json={}, headers=auth)
    run_jobs()
    exports = client.get(f"/api/v1/clips/{clip['id']}/exports", headers=auth).json()
    export = [item for item in exports if item["kind"] == "video"][0]
    link = client.get(f"/api/v1/exports/{export['id']}/download", headers=auth)
    assert link.status_code == 200
    path = Path(link.json()["filename"])
    assert path.suffix == ".mp4"
