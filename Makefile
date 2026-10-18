.PHONY: all data media world derive validate

all: data

data:
	scripts/generate_all.sh

media:
	cd generators && python3 gen_media.py --force

world:
	cd generators && python3 gen_world.py

derive:
	cd generators && python3 derive_events.py

validate:
	cd generators && python3 validate.py
