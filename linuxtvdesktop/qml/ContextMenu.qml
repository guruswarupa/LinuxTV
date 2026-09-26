import QtQuick 2.15

Item {
    id: root
    property int currentIndex: 0
    property var actions: {
        var list = []
        if (backend.menuState && backend.menuState.favorited) {
            list.push({ key: "favorite", label: "Remove from Favorites" })
        } else {
            list.push({ key: "favorite", label: "Add to Favorites" })
        }
        if (backend.menuState && backend.menuState.canMoveLeft) {
            list.push({ key: "moveLeft", label: "Move Left" })
        }
        if (backend.menuState && backend.menuState.canMoveRight) {
            list.push({ key: "moveRight", label: "Move Right" })
        }
        list.push({ key: "edit", label: "Edit" })
        list.push({ key: "remove", label: "Remove" })
        return list
    }

    visible: opacity > 0
    opacity: backend.menuOpen ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: 150 } }
    onVisibleChanged: if (visible) currentIndex = 0

    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(0, 0, 0, 0.45)
        MouseArea { anchors.fill: parent; onClicked: backend.closeMenu() }
    }

    Rectangle {
        id: card
        width: 300
        anchors.centerIn: parent
        radius: 14
        color: theme.surface
        height: column.height + 32

        Column {
            id: column
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.margins: 16
            spacing: 4

            Text {
                text: (backend.menuState && backend.menuState.name) ? backend.menuState.name : ""
                color: theme.text
                font.family: theme.font_family
                font.pixelSize: 17
                font.bold: true
                bottomPadding: 8
                elide: Text.ElideRight
                width: column.width
            }

            Repeater {
                model: root.actions
                delegate: Rectangle {
                    width: column.width
                    height: 42
                    radius: 8
                    color: root.currentIndex === index ? theme.accent : "transparent"

                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.left: parent.left
                        anchors.leftMargin: 10
                        text: modelData.label
                        color: theme.text
                        font.family: theme.font_family
                        font.pixelSize: 15
                    }

                    MouseArea {
                        anchors.fill: parent
                        onClicked: {
                            root.currentIndex = index
                            backend.menuAction(modelData.key)
                        }
                    }
                }
            }
        }
    }

    focus: backend.menuOpen
    Keys.onPressed: (event) => {
        switch (event.key) {
        case Qt.Key_Up:
            root.currentIndex = Math.max(0, root.currentIndex - 1)
            event.accepted = true
            break
        case Qt.Key_Down:
            root.currentIndex = Math.min(root.actions.length - 1, root.currentIndex + 1)
            event.accepted = true
            break
        case Qt.Key_Return:
        case Qt.Key_Enter:
            backend.menuAction(root.actions[root.currentIndex].key)
            event.accepted = true
            break
        case Qt.Key_Escape:
        case Qt.Key_Back:
            backend.closeMenu()
            event.accepted = true
            break
        default:
            event.accepted = false
        }
    }
}
