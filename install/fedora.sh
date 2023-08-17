#!/bin/sh

sudo -v

echo "Setting up Fedora..."

# Fedora
if type "dnf" > /dev/null; then
	if ! dnf list installed "ibm-plex-fonts-all" > /dev/null 2>&1; then
		echo "Installing IBM Plex Fonts..."
		sudo dnf install -y ibm-plex-fonts-all
	fi

	if ! type "zsh" > /dev/null; then
		echo "Installing zsh..."
		sudo dnf install -y zsh
		# make zsh default shell on Fedora
		sudo lchsh $USER
	fi

	if ! type "kitty" > /dev/null; then
		echo "Installing kitty..."
		sudo dnf install -y kitty
	fi
fi

if [ ! -d "$HOME/git" ]; then
	mkdir $HOME/git
fi

git config --global user.name "Thibaut Remy"
git config --global user.email "thibaut@thibaut.re"

# diff-so-fancy
git config --global core.pager "diff-so-fancy | less --tabs=4 -RFX"
git config --global interactive.diffFilter "diff-so-fancy --patch"
git config --global color.ui true

git config --global color.diff-highlight.oldNormal    "red bold"
git config --global color.diff-highlight.oldHighlight "red bold 52"
git config --global color.diff-highlight.newNormal    "green bold"
git config --global color.diff-highlight.newHighlight "green bold 22"

git config --global color.diff.meta       "11"
git config --global color.diff.frag       "magenta bold"
git config --global color.diff.func       "146 bold"
git config --global color.diff.commit     "yellow bold"
git config --global color.diff.old        "red bold"
git config --global color.diff.new        "green bold"
git config --global color.diff.whitespace "red reverse"

# setup zsh-syntax-highlighting as plugin of ohmyzsh
if [ ! -d ohmyzsh/custom/plugins/zsh-syntax-highlighting ]; then
	ln -s -r zsh-syntax-highlighting ohmyzsh/custom/plugins/zsh-syntax-highlighting
fi

# setup dotfiles
echo "Setting up symlink to dotfiles..."

# zshrc
rm -rf $HOME/.zshrc
ln -s -r zshrc $HOME/.zshrc

# helix editor
rm -rf $HOME/.config/helix
mkdir $HOME/.config/helix
mkdir $HOME/.config/helix/themes
ln -s -r helix/config.toml $HOME/.config/helix/config.toml
ln -s -r helix/themes/autumn_night_transparent.toml $HOME/.config/helix/themes/autumn_night_transparent.toml

# kitty terminal
rm -rf $HOME/.config/kitty
mkdir $HOME/.config/kitty
ln -s -r kitty/kitty.conf $HOME/.config/kitty/kitty.conf
ln -s -r kitty/current-theme.conf $HOME/.config/kitty/current-theme.conf

