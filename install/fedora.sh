#!/bin/sh

sudo -v

echo "Setting up Fedora..."

# Fedora
if ! type "dnf" > /dev/null; then
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

	if test ! $(which omz); then
		echo "Installing ohmyzsh..."
		zsh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
	fi
fi

if [ ! -d "$HOME/git" ]; then
	mkdir $HOME/git
fi

git config --global user.name "Thibaut Remy"
git config --global user.email "thibaut@thibaut.re"

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

