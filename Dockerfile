FROM debian:trixie

# Install required dependencies
RUN apt-get update && apt-get install -y \
    live-build \
    rsync \
    && apt-get clean

# Set up working directory
WORKDIR /LinuxTV

# Copy the entire project
COPY . /LinuxTV

# Set permissions for the build script
RUN chmod +x /LinuxTV/iso-builder/build-iso.sh

# Switch to root for the build process
USER root

# Run the ISO build
CMD ["/LinuxTV/iso-builder/build-iso.sh"]