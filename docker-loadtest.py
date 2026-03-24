import docker
import argparse
import sys

parser = argparse.ArgumentParser(description='Spin up multiple instances of a container for testing')
parser.add_argument('-c','--count', help='Container Count', required=False, type=int, default=10)
parser.add_argument('-s','--start', help='Start', required=False, action='store_true')
parser.add_argument('-k','--kill', help='Kill', required=False, action='store_true')
parser.add_argument('-v','--volume', help='Volume mapping host:container', required=False, default='/home/trevor/redfish-mockups/ssl:/certs')
parser.add_argument('-r','--redfish', help='Redfish mapping host:container (mounted at /redfish)', required=False, default='/home/trevor/redfish-mockups/R7615_iDRAC_7.20.80.50/redfish:/redfish')
parser.add_argument('-n','--network', help='Docker network to attach containers to', required=False, default='agentfish_agentfish')
parser.add_argument('-b','--build', help='Force building the image before starting', required=False, action='store_true')
parser.add_argument('-l','--listener', help='Listener URL to set as LISTENER_DEST in containers', required=False, default='http://redfish-listener:8080/redfish/events')
args = parser.parse_args()

client = docker.from_env()

IMAGE_NAME = "redfish-testserver"
CONTAINER_NAME_PREFIX = "redfish-testserver-"
BUILD_CONTEXT = "./test-server"

if args.start:
    # ghcr.io/trevorsquillario/idrac-sse:latest
    # ensure image exists, build if missing or if forced
    if args.build:
        # remove any existing test containers so we can rebuild cleanly
        print(f"Force building image {IMAGE_NAME} from {BUILD_CONTEXT}")
        try:
            existing = client.containers.list(all=True)
            for c in existing:
                if c.name.startswith(CONTAINER_NAME_PREFIX):
                    print(f"Removing existing container {c.name} due to forced rebuild")
                    try:
                        c.remove(force=True)
                    except Exception as e:
                        print(f"Failed to remove {c.name}: {e}")
        except Exception as e:
            print(f"Error enumerating containers: {e}")

        # remove existing image to be certain we rebuild from scratch
        try:
            client.images.remove(IMAGE_NAME, force=True)
            print(f"Removed existing image {IMAGE_NAME}")
        except Exception:
            pass

        try:
            image, build_logs = client.images.build(path=BUILD_CONTEXT, tag=IMAGE_NAME)
            print(f"Built image {IMAGE_NAME}")
        except Exception as e:
            print(f"Failed to build image: {e}")
            sys.exit(1)
    else:
        try:
            client.images.get(IMAGE_NAME)
            print(f"Image {IMAGE_NAME} found locally")
        except docker.errors.ImageNotFound:
            print(f"Image {IMAGE_NAME} not found locally — building from {BUILD_CONTEXT}")
            try:
                image, build_logs = client.images.build(path=BUILD_CONTEXT, tag=IMAGE_NAME)
                print(f"Built image {IMAGE_NAME}")
            except Exception as e:
                print(f"Failed to build image: {e}")
                sys.exit(1)

    vols = None
    if args.volume:
        # support comma-separated host:container[:mode] mappings
        if ':' in args.volume:
            vols = {}
            mappings = [m.strip() for m in args.volume.split(',') if m.strip()]
            for m in mappings:
                parts = m.split(':')
                if len(parts) >= 2:
                    host = parts[0]
                    container = parts[1]
                    mode = parts[2] if len(parts) >= 3 else 'rw'
                    vols[host] = {'bind': container, 'mode': mode}
        else:
            # fallback: treat as container-only volume (anonymous)
            vols = [args.volume]

    network = args.network
    # ensure network exists
    nets = client.networks.list(names=[network])
    if not nets:
        print(f"Creating network {network}")
        client.networks.create(network, driver="bridge")

    for i in range(args.count):
        run_kwargs = {
            "name": f"{CONTAINER_NAME_PREFIX}{i}",
            "network": network,
            "detach": True,
        }
        # Inject redfish mount if requested
        if args.redfish:
            # parse host:container[:mode]
            rparts = args.redfish.split(':')
            if len(rparts) >= 2:
                rhost = rparts[0]
                rcontainer = rparts[1]
                rmode = rparts[2] if len(rparts) >= 3 else 'ro'
            else:
                # fallback to mapping host -> /redfish
                rhost = args.redfish
                rcontainer = '/redfish'
                rmode = 'ro'
            if vols is None:
                vols = {}
            vols[rhost] = {'bind': rcontainer, 'mode': rmode}
        # inject listener env into the container so test server forwards to the listener
        run_kwargs["environment"] = {"LISTENER_DEST": args.listener}
        if vols:
            run_kwargs["volumes"] = vols

        client.containers.run(IMAGE_NAME, **run_kwargs)

    print([c.name for c in client.containers.list(all=True) if c.name.startswith(CONTAINER_NAME_PREFIX)])

if args.kill:
    containers = client.containers.list(all=True)
    for c in containers:
        if c.name.startswith(CONTAINER_NAME_PREFIX):
            print(f"Removing {c.name}")
            c.remove(force=True)