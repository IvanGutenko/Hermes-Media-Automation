"""Host-side preparation of Instagram media: feed photos get a legal aspect ratio, nothing else changes."""
import hashlib,json
from pathlib import Path
from script_generator import BASE_DIR
from youtube_uploader import save
import instagram_format as fmt


def prepare_image(source,key):
    """Return the source itself when Instagram accepts it, else a 4:5 / 1.91:1 copy on a blurred backdrop."""
    from autonomous_video_pipeline import docker_command,container_path,probe
    source=Path(source).resolve()
    stream=next(s for s in probe(source)['streams'] if s.get('codec_type')=='video')
    graph=fmt.fit_filter(int(stream['width']),int(stream['height']))
    if not graph:return str(source)
    folder=BASE_DIR/'output/instagram_assets';folder.mkdir(exist_ok=True)
    target=folder/(key+'.jpg');receipt=folder/(key+'.json')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if receipt.exists() and target.exists():
        if json.loads(receipt.read_text()).get('source_sha256')!=digest:raise RuntimeError('Instagram image belongs to a different source')
        return str(target)
    docker_command('ffmpeg','-v','error','-y','-i',container_path(source),'-filter_complex',graph,
        '-frames:v','1','-q:v','2',container_path(target))
    out=next(s for s in probe(target)['streams'] if s.get('codec_type')=='video')
    if fmt.photo_target(int(out['width']),int(out['height'])):raise RuntimeError('Prepared Instagram image still has an unsupported ratio')
    save(receipt,{'source_sha256':digest,'source':str(source),'width':int(out['width']),'height':int(out['height'])})
    return str(target)
