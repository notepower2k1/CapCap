"""Clean generated data and report which projects still exist after failures."""
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time


@dataclass
class CleanupResult:
    projects_checked: bool = False
    remaining_project_ids: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)


def clean_generated_video_data(workspace, *, process_events=lambda: None):
    root=Path(workspace).resolve()
    result=CleanupResult()
    for name in ("projects","temp"):
        directory=root/name
        if not directory.exists():
            directory.mkdir(parents=True,exist_ok=True)
            if name=="projects":result.projects_checked=True
            continue
        # Never follow a cache/project junction outside the writable workspace.
        if not directory.resolve().is_relative_to(root):
            result.errors.append(f"{directory}: outside workspace")
            continue
        for target in list(directory.iterdir()):
            if target.name==".gitkeep" or (name=="temp" and target.suffix.lower()==".log"):
                continue
            if not target.resolve().is_relative_to(directory.resolve()):
                result.errors.append(f"{target}: outside {name}")
                continue
            for attempt in range(4):
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink(missing_ok=True)
                    break
                except FileNotFoundError:
                    break
                except OSError as exc:
                    if attempt==3:
                        result.errors.append(f"{target}: {exc}")
                    else:
                        process_events()
                        time.sleep(.1*(attempt+1))
        if name=="projects":
            result.remaining_project_ids={path.name for path in directory.iterdir() if path.is_dir()}
            result.projects_checked=True
    return result


def retain_existing_project_entries(entries, service, remaining_project_ids, *, video_field):
    return [entry for entry in entries
            if service._build_project_id(entry.get(video_field,"")) in remaining_project_ids]
