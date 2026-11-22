"""Build the deterministic private runtime bundle from the frozen archive."""
import argparse
import hashlib
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from zrs_study import BUNDLE, prepare


def build(archive, target):
    with tempfile.TemporaryDirectory(prefix='zrs-bundle-') as temporary, ZipFile(target, 'w') as output:
        for mode in ('synthetic-demo', 'E14'):
            root = Path(temporary) / mode
            root.mkdir()
            prepare(root, archive, e14=mode == 'E14')
            for file in sorted(root.rglob('*')):
                if file.is_file():
                    info = ZipInfo(mode + '/' + file.relative_to(root).as_posix())
                    output.writestr(info, file.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=9)
    return hashlib.sha256(target.read_bytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--output', type=Path, default=BUNDLE)
    args = parser.parse_args()
    digest = build(args.archive.resolve(), args.output)
    args.output.with_suffix('.sha256').write_bytes((digest + '\n').encode('ascii'))
    print(digest)
