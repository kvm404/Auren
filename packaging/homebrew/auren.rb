# Auren for macOS, from Homebrew: brew install kvm404/auren/auren, then auren setup.
#
# The source of truth is packaging/homebrew/auren.rb in https://github.com/kvm404/Auren. The brew
# workflow (.github/workflows/brew.yml) fills in the release's url and the checksum of GitHub's source
# tarball for its tag, and pushes it to the tap https://github.com/kvm404/homebrew-auren.
#
# Homebrew installs the source and two small commands. `auren setup` runs install-macos.sh --brew from
# that source: it builds Auren's Python environment in ~/.local/share/auren, Auren.app and its launch
# agents for your user, which a formula can't do (it asks for the macOS permissions).
class Auren < Formula
  desc "Voice assistant: say what you want, and it uses your Mac"
  homepage "https://github.com/kvm404/Auren"
  url "https://github.com/kvm404/Auren/archive/refs/tags/v0.1.7.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  depends_on arch: :arm64
  depends_on macos: :sonoma
  depends_on "uv"

  def install
    # In a folder of its own: Homebrew moves README, LICENSE and CHANGELOG out of libexec itself (as the
    # keg's metadata), and building Auren's environment needs them next to pyproject.toml.
    (libexec/"source").install Dir["*"]
    wrapper = (libexec/"source/packaging/homebrew/wrapper.sh").read
    %w[auren auren-ctl].each do |cmd|
      (bin/cmd).write wrapper.gsub("@COMMAND@", cmd)
                             .gsub("@VERSION@", version.to_s)
                             .gsub("@LIBEXEC@", (opt_libexec/"source").to_s)
                             .gsub("@BREW_BIN@", "#{HOMEBREW_PREFIX}/bin")
      (bin/cmd).chmod 0755
    end
  end

  def caveats
    <<~EOS
      To finish, run:
        auren setup
      It sets Auren up for your user: its voice, Auren.app and the login items, and opens
      Auren's setup window, which asks for the macOS permissions. Auren needs Claude Code
      or Codex as its brain.

      After brew upgrade auren, run auren setup again (auren update does both).
    EOS
  end

  test do
    assert_path_exists libexec/"source/install-macos.sh"
    assert_path_exists libexec/"source/README.md"
    assert_match "isn't set up yet", shell_output("#{bin}/auren 2>&1", 1)
    assert_match "isn't set up yet", shell_output("#{bin}/auren-ctl status 2>&1", 1)
    # setup reaches the installer, which refuses an unknown option before doing anything
    assert_match "usage:", shell_output("#{bin}/auren setup --not-an-option 2>&1", 2)
  end
end
